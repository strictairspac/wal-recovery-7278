"""采样站整批写入的幂等入账服务。

关键保证：
1. 同批次号 + 同内容哈希的重试不重复入账，返回首份回执（含 idempotent/replay 标记）。
2. 同批次号但内容变化一律 409 拒绝，绝不覆盖旧批。
3. 回执与账本流水、余额在同一个数据库事务内提交（同生共死）。
4. 原批未提交就断电：事务回滚后只可能留下 processing 残留行且没有任何流水，
   重试直接接管并完成入账，避免数据丢失。
5. 已提交旧批跨检查点仍可凭批次号认出（持久表，无内存状态）。
"""
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import BatchWrite, LedgerAccount, LedgerEntry
from src.utils.serialize import record

def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def content_hash(items: list[dict]) -> str:
    # 以稳定的 JSON 序列化定义“同内容”：键顺序、数组元素顺序都不影响判定，
    # 与入账时同键合并、按 key 排序生成流水的确定性效果保持一致
    canonical_items = sorted(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        for item in items
    )
    canonical = json.dumps(canonical_items, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()

def _validate_items(items: list[dict]):
    if not items:
        raise HTTPException(422, '批次至少包含一条写入')
    if len(items) > 30:
        raise HTTPException(422, '单批最多30条写入')
    for item in items:
        if not isinstance(item.get('delta'), int) or isinstance(item.get('delta'), bool):
            raise HTTPException(422, '入账增量必须为整数')
        if abs(item['delta']) > 1_000_000_000:
            raise HTTPException(422, '单条增量绝对值不超过10亿')

def _build_receipt(batch: BatchWrite, replay: bool, entries: list[LedgerEntry]) -> dict:
    return {
        'batch_no': batch.batch_no,
        'station': batch.station,
        'status': batch.status,
        'attempts': batch.attempts,
        'content_hash': batch.content_hash,
        'idempotent': True,
        'replay': replay,
        'entry_count': len(entries),
        'entries': [
            {'key': e.key, 'delta': e.delta, 'balance_after': e.balance_after}
            for e in entries
        ],
        'committed_at': batch.committed_at.isoformat() if batch.committed_at else None,
        'receipt_no': f'RC-{batch.id}-{batch.content_hash[:10]}',
    }

def _get_or_404(db, batch_no: str) -> BatchWrite:
    batch = db.scalar(select(BatchWrite).where(BatchWrite.batch_no == batch_no))
    if batch is None:
        raise HTTPException(404, '批次不存在，请先录入批次')
    return batch

def list_batches(db):
    rows = db.scalars(select(BatchWrite).order_by(BatchWrite.id.desc()).limit(100)).all()
    return [record(b) for b in rows]

def get_batch(db, batch_no: str):
    return record(_get_or_404(db, batch_no))

def submit_batch(db, data, crash: str | None = None):
    """提交（或重试）一个批次。返回 (批次记录, 是否重放)。调用方负责外层 commit。"""
    items = [item.model_dump() for item in data.items]
    _validate_items(items)
    digest = content_hash(items)
    batch = db.scalar(select(BatchWrite).where(BatchWrite.batch_no == data.batch_no))

    if batch is not None:
        # 换了内容就拒绝：无论旧批是否已提交，批次号内容绑定不可变
        if batch.content_hash != digest:
            raise HTTPException(409, f'批次号 {data.batch_no} 已存在但内容不一致，拒绝覆盖入账')
        if batch.status == 'committed':
            # 同批同内容重试：不重复入账，返回原回执
            entries = db.scalars(
                select(LedgerEntry).where(LedgerEntry.batch_id == batch.id).order_by(LedgerEntry.id)
            ).all()
            batch.attempts += 1
            db.flush()
            return batch, True, _build_receipt(batch, True, entries)
        # processing：原批未结束（含断电残留），本次重试接管，下面走一次全新入账

    if batch is None:
        batch = BatchWrite(
            batch_no=data.batch_no, station=data.station, content_hash=digest,
            payload=items, status='processing', attempts=0,
        )
        db.add(batch)
        db.flush()
    else:
        # 残留批次：确认没有任何流水（原子事务保证），补齐本次载荷后接管
        leftovers = db.scalars(select(LedgerEntry).where(LedgerEntry.batch_id == batch.id)).all()
        if leftovers:
            raise HTTPException(500, '残留批次存在入账流水，数据状态异常，已阻止重复入账')
        batch.payload = items
        batch.station = data.station

    batch.attempts += 1

    # 故障注入：提交前“断电”——主动回滚全部账本改动与批次进度，
    # 只保留一个不含任何流水的 processing 残留，模拟崩溃后的磁盘状态
    if crash == 'rollback':
        db.rollback()
        stub = db.scalar(select(BatchWrite).where(BatchWrite.batch_no == data.batch_no))
        if stub is None:
            db.add(BatchWrite(
                batch_no=data.batch_no, station=data.station, content_hash=digest,
                payload=items, status='processing', attempts=1,
            ))
            db.commit()
        raise HTTPException(503, '已模拟提交前断电：本批未入账，请用同一批次号重发整批以恢复入账')

    # 同键 delta 合并，减少账户行与流水行的争用
    merged: dict[str, int] = {}
    for item in items:
        merged[item['key']] = merged.get(item['key'], 0) + item['delta']

    entries: list[LedgerEntry] = []
    for key in sorted(merged):
        delta = merged[key]
        account = db.scalar(select(LedgerAccount).where(LedgerAccount.key == key).with_for_update())
        if account is None:
            # 不同批次并发创建同一新账户：唯一约束兜底，保存点让本请求自愈而不是整单失败
            sp = db.begin_nested()
            try:
                account = LedgerAccount(key=key, balance=0)
                db.add(account)
                db.flush()
            except IntegrityError:
                sp.rollback()
                account = db.scalar(
                    select(LedgerAccount).where(LedgerAccount.key == key).with_for_update()
                )
        new_balance = account.balance + delta
        if new_balance < 0:
            raise HTTPException(422, f'账户 {key} 入账后余额为负（{new_balance}），整批拒绝')
        account.balance = new_balance
        entry = LedgerEntry(
            batch_id=batch.id, batch_no=batch.batch_no, key=key,
            delta=delta, balance_after=new_balance,
        )
        db.add(entry)
        entries.append(entry)

    batch.status = 'committed'
    batch.committed_at = now()
    # 回执与账本在同一事务内构建并随调用方一起提交
    db.flush()
    batch.receipt = _build_receipt(batch, False, entries)

    # 故障注入：回执“丢失”——先提交账本与回执，再向调用方报错，
    # 采样站收不到回执会重发，重发命中幂等分支拿回同一份回执
    if crash == 'stuck':
        db.commit()
        raise HTTPException(503, '已模拟回执丢失：账本与回执已一起入账，请用同一批次号重发取回原回执')

    return batch, False, batch.receipt

def list_ledger(db):
    accounts = db.scalars(select(LedgerAccount).order_by(LedgerAccount.key)).all()
    return [record(a) for a in accounts]

def list_entries(db, batch_no: str | None = None):
    stmt = select(LedgerEntry).order_by(LedgerEntry.id.desc()).limit(200)
    if batch_no:
        stmt = select(LedgerEntry).where(LedgerEntry.batch_no == batch_no).order_by(LedgerEntry.id)
    return [record(e) for e in db.scalars(stmt).all()]
