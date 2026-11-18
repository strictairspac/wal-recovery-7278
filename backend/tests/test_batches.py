import json
import pathlib
import threading

from fastapi.testclient import TestClient


def _payload(batch_no, items=None, station='station-01'):
    return {
        'batch_no': batch_no,
        'station': station,
        'items': items if items is not None else [
            {'key': 'sample_A', 'delta': 10},
            {'key': 'sample_B', 'delta': 5},
        ],
    }


def _balances(client, headers):
    return {a['key']: a['balance']
            for a in client.get('/ledger/accounts', headers=headers).json()}


def test_first_submit_commits_receipt_and_ledger_together(client, auth_headers):
    r = client.post('/batches', json=_payload('B-1001'), headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['replay'] is False
    receipt = body['receipt']
    assert receipt['idempotent'] is True and receipt['replay'] is False
    assert receipt['receipt_no'].startswith('RC-')
    assert {e['key']: e['balance_after'] for e in receipt['entries']} == {
        'sample_A': 10, 'sample_B': 5,
    }
    # 回执随批次持久化，账本账户同时生效
    got = client.get('/batches/B-1001', headers=auth_headers).json()
    assert got['status'] == 'committed'
    assert got['receipt']['receipt_no'] == receipt['receipt_no']
    assert _balances(client, auth_headers) == {'sample_A': 10, 'sample_B': 5}
    entries = client.get('/ledger/entries?batch_no=B-1001', headers=auth_headers).json()
    assert len(entries) == 2


def test_same_batch_same_content_retry_is_idempotent(client, auth_headers):
    client.post('/batches', json=_payload('B-2001'), headers=auth_headers)
    r2 = client.post('/batches', json=_payload('B-2001'), headers=auth_headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body['replay'] is True
    assert body['receipt']['replay'] is True
    first = client.get('/batches/B-2001', headers=auth_headers).json()
    # 回执仍是首份：receipt_no 不变，attempts 增加
    assert first['receipt']['receipt_no'] == body['receipt']['receipt_no']
    assert first['attempts'] == 2
    # 账本没有重复入账，流水仍只有两条
    assert _balances(client, auth_headers) == {'sample_A': 10, 'sample_B': 5}
    entries = client.get('/ledger/entries?batch_no=B-2001', headers=auth_headers).json()
    assert len(entries) == 2


def test_reordered_same_content_still_replays(client, auth_headers):
    first = [{'key': 'a', 'delta': 1}, {'key': 'b', 'delta': 2}]
    reordered = [{'key': 'b', 'delta': 2}, {'key': 'a', 'delta': 1}]
    client.post('/batches', json=_payload('B-ORDER', first), headers=auth_headers)
    r = client.post('/batches', json=_payload('B-ORDER', reordered), headers=auth_headers)
    assert r.status_code == 200 and r.json()['replay'] is True
    # 不同 key/value 组合仍然冲突
    changed = client.post('/batches', json=_payload('B-ORDER', [{'key': 'a', 'delta': 1}, {'key': 'b', 'delta': 3}]),
                          headers=auth_headers)
    assert changed.status_code == 409


def test_same_batch_changed_content_is_rejected(client, auth_headers):
    client.post('/batches', json=_payload('B-3001'), headers=auth_headers)
    changed = _payload('B-3001', [{'key': 'sample_A', 'delta': 999}])
    r = client.post('/batches', json=changed, headers=auth_headers)
    assert r.status_code == 409 and '内容不一致' in r.json()['detail']
    # 原回执与原账本保持不变
    assert _balances(client, auth_headers) == {'sample_A': 10, 'sample_B': 5}
    got = client.get('/batches/B-3001', headers=auth_headers).json()
    assert got['attempts'] == 1


def test_concurrent_duplicate_submits_only_commit_once(client, auth_headers, session_factory):
    results, errors = [], []

    def fire():
        try:
            local = TestClient(app=client.app)
            resp = local.post('/batches', json=_payload('B-CONC'), headers=auth_headers)
            results.append((resp.status_code, resp.json() if resp.status_code == 200 else resp.text))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=fire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(results) == 6 and all(code == 200 for code, _ in results)
    assert sum(1 for _, body in results if body['replay']) == 5  # 只认一份提交
    with session_factory() as db:
        from sqlalchemy import select, func
        from src.models.entities import BatchWrite, LedgerEntry, LedgerAccount
        assert db.scalar(select(func.count()).select_from(BatchWrite)
                         .where(BatchWrite.batch_no == 'B-CONC')) == 1
        assert db.scalar(select(func.count()).select_from(LedgerEntry)
                         .where(LedgerEntry.batch_no == 'B-CONC')) == 2
        assert db.scalar(select(LedgerAccount).where(LedgerAccount.key == 'sample_A')).balance == 10


def test_concurrent_different_batches_same_new_account_both_commit(client, auth_headers):
    # 不同批次号、同一新键并发：账户唯一约束兜底，保存点自愈，两批都应入账
    results, errors = [], []

    def fire(no):
        try:
            local = TestClient(app=client.app)
            resp = local.post('/batches',
                              json=_payload(f'B-NEW-{no}', [{'key': 'fresh_key', 'delta': no}]),
                              headers=auth_headers)
            results.append(resp.status_code)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and sorted(results) == [200, 200, 200, 200]
    balances = _balances(client, auth_headers)
    assert balances['fresh_key'] == 1 + 2 + 3 + 4  # 四个批次各入各的，互不覆盖


def test_rollback_crash_leaves_no_entries_and_retry_recovers(client, auth_headers):
    crash = client.post('/batches/crash-demo',
                        json={**_payload('B-CRASH-1'), 'action': 'rollback'},
                        headers=auth_headers)
    assert crash.status_code == 503
    # 断电后：批次残留 processing，但无任何流水、账本未动
    stale = client.get('/batches/B-CRASH-1', headers=auth_headers).json()
    assert stale['status'] == 'processing' and stale['receipt'] is None
    assert client.get('/ledger/entries?batch_no=B-CRASH-1', headers=auth_headers).json() == []
    assert _balances(client, auth_headers) == {}

    # 恢复后用同一批次号同内容重试：成功入账，数据不丢
    retry = client.post('/batches', json=_payload('B-CRASH-1'), headers=auth_headers)
    assert retry.status_code == 200
    body = retry.json()
    assert body['replay'] is False and body['batch']['status'] == 'committed'
    assert body['batch']['attempts'] == 2
    assert _balances(client, auth_headers) == {'sample_A': 10, 'sample_B': 5}
    entries = client.get('/ledger/entries?batch_no=B-CRASH-1', headers=auth_headers).json()
    assert len(entries) == 2
    # 再次重发仍是幂等
    again = client.post('/batches', json=_payload('B-CRASH-1'), headers=auth_headers).json()
    assert again['replay'] is True
    assert _balances(client, auth_headers) == {'sample_A': 10, 'sample_B': 5}


def test_stuck_crash_loses_receipt_but_ledger_is_safe(client, auth_headers):
    crash = client.post('/batches/crash-demo',
                        json={**_payload('B-CRASH-2'), 'action': 'stuck'},
                        headers=auth_headers)
    assert crash.status_code == 503
    # 回执丢了，但回执与账本已一起提交
    committed = client.get('/batches/B-CRASH-2', headers=auth_headers).json()
    assert committed['status'] == 'committed' and committed['receipt'] is not None
    assert _balances(client, auth_headers) == {'sample_A': 10, 'sample_B': 5}

    # 采样站重发：拿回原回执，不重复入账
    retry = client.post('/batches', json=_payload('B-CRASH-2'), headers=auth_headers)
    assert retry.status_code == 200 and retry.json()['replay'] is True
    assert retry.json()['receipt']['receipt_no'] == committed['receipt']['receipt_no']
    assert _balances(client, auth_headers) == {'sample_A': 10, 'sample_B': 5}
    assert len(client.get('/ledger/entries?batch_no=B-CRASH-2',
                          headers=auth_headers).json()) == 2


def test_changed_content_after_crash_is_rejected(client, auth_headers):
    client.post('/batches/crash-demo',
                json={**_payload('B-CRASH-3'), 'action': 'rollback'},
                headers=auth_headers)
    changed = _payload('B-CRASH-3', [{'key': 'sample_A', 'delta': 42}])
    r = client.post('/batches', json=changed, headers=auth_headers)
    assert r.status_code == 409
    assert client.get('/batches/B-CRASH-3', headers=auth_headers).json()['status'] == 'processing'


def test_negative_balance_rejects_whole_batch(client, auth_headers):
    r = client.post('/batches',
                    json=_payload('B-NEG', [{'key': 'sample_A', 'delta': -1}]),
                    headers=auth_headers)
    assert r.status_code == 422 and '余额为负' in r.json()['detail']
    assert _balances(client, auth_headers) == {}
    assert client.get('/batches/B-NEG', headers=auth_headers).status_code == 404


def test_viewer_cannot_submit_and_ledger_is_readable(client, auth_headers, viewer_headers):
    denied = client.post('/batches', json=_payload('B-RO'), headers=viewer_headers)
    assert denied.status_code == 403
    assert client.get('/ledger/accounts', headers=viewer_headers).status_code == 200
    assert client.get('/batches', headers=viewer_headers).status_code == 200


def test_audits_recorded_for_commit_replay_and_crash(client, auth_headers, session_factory):
    client.post('/batches', json=_payload('B-AUDIT'), headers=auth_headers)
    client.post('/batches', json=_payload('B-AUDIT'), headers=auth_headers)
    r = client.post('/batches/crash-demo',
                    json={**_payload('B-AUDIT-CRASH'), 'action': 'rollback'},
                    headers=auth_headers)
    assert r.status_code == 503
    with session_factory() as db:
        from src.models.entities import Audit
        actions = [a.action for a in db.query(Audit).all()]
    assert 'batch:commit:B-AUDIT' in actions
    assert 'batch:replay:B-AUDIT' in actions
    assert 'batch:crash-rollback:B-AUDIT-CRASH' in actions


def test_receipt_export_and_original_recovery_download_coexist(client, auth_headers):
    client.post('/batches', json=_payload('B-EXPORT'), headers=auth_headers)
    r = client.get('/batches/B-EXPORT/export', headers=auth_headers)
    assert r.status_code == 200
    assert 'attachment' in r.headers['content-disposition']
    exported = json.loads(r.content)
    assert exported['batch_no'] == 'B-EXPORT' and exported['receipt']
    # 原有恢复演练下载路由仍在
    paths = {route.path for route in client.app.routes}
    assert '/recoveries/{ident}/export' in paths
    assert '/batches/{batch_no}/export' in paths


def test_committed_batch_survives_restart_and_still_replays(tmp_path):
    """跨检查点/跨进程：关闭并重开数据库后，旧批次号仍被认出，重试只回原回执。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.config import database as db_module
    from src.config.database import Base
    from src.models.entities import LedgerAccount  # noqa: F401  注册表
    from src.main import app
    from fastapi.testclient import TestClient as TC

    db_file = tmp_path / 'durable.db'
    engine = create_engine(f'sqlite:///{db_file}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    old_engine, old_session = db_module.engine, db_module.Session
    db_module.engine, db_module.Session = engine, factory

    from src.config.database import get_db
    def override_get_db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = override_get_db

    from tests.conftest import _login
    token = _login(TC(app), factory, 'operator')
    headers = {'Authorization': f'Bearer {token}'}
    try:
        c = TC(app)
        c.post('/batches', json=_payload('B-RESTART'), headers=headers)
        engine.dispose()  # 模拟断电后进程结束、检查点落盘

        engine2 = create_engine(f'sqlite:///{db_file}')
        factory2 = sessionmaker(bind=engine2, expire_on_commit=False)
        db_module.engine, db_module.Session = engine2, factory2
        c2 = TC(app)
        # 旧批仍被认出：同内容重放
        replay = c2.post('/batches', json=_payload('B-RESTART'), headers=headers)
        assert replay.status_code == 200 and replay.json()['replay'] is True
        # 换内容依然拒绝
        changed = c2.post('/batches',
                          json=_payload('B-RESTART', [{'key': 'sample_A', 'delta': 7}]),
                          headers=headers)
        assert changed.status_code == 409
        assert {a['key']: a['balance']
                for a in c2.get('/ledger/accounts', headers=headers).json()} == {
            'sample_A': 10, 'sample_B': 5}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_module.engine, db_module.Session = old_engine, old_session
