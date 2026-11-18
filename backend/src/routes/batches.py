from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
import json
from src.config.database import get_db
from src.middlewares.auth import current_user, writable
from src.schemas.batch import BatchWriteInput, CrashDemoInput
from src.services import batch_service
from src.controllers.actions import audit
from src.utils.locks import batch_guard
from src.utils.serialize import record

router = APIRouter()

@router.get('/batches')
def list_batches(db=Depends(get_db), user=Depends(current_user)):
    return batch_service.list_batches(db)

@router.post('/batches/crash-demo', status_code=200)
def crash_demo(data: CrashDemoInput, db=Depends(get_db), user=Depends(writable)):
    """断电演练：以指定批次号提交并注入故障，随后可在页面用同号重试核对恢复。"""
    action = data.action
    payload = BatchWriteInput(**data.model_dump(exclude={'action'}))
    with batch_guard(payload.batch_no):
        try:
            # 故障注入以 503 表达“采样站收不到回执”，FastAPI 会保留该状态码
            batch_service.submit_batch(db, payload, crash=action)
        except HTTPException:
            audit(db, user, f'batch:crash-{action}:{payload.batch_no}')
            db.commit()
            raise
    return {'detail': '未触发故障'}  # 正常不可达：注入故障时 submit_batch 必抛异常

@router.get('/batches/{batch_no}')
def get_batch(batch_no: str, db=Depends(get_db), user=Depends(current_user)):
    return batch_service.get_batch(db, batch_no)

@router.post('/batches', status_code=200)
def submit(data: BatchWriteInput, db=Depends(get_db), user=Depends(writable)):
    # 同批次号全局串行：并发重发只放一份进入临界区，其余等待幂等结果
    with batch_guard(data.batch_no):
        batch, replay, receipt = batch_service.submit_batch(db, data)
        audit(db, user, f'batch:replay:{data.batch_no}' if replay else f'batch:commit:{data.batch_no}')
        db.commit()
    return {'batch': record(batch), 'receipt': receipt, 'replay': replay}

@router.get('/batches/{batch_no}/export')
def export_receipt(batch_no: str, db=Depends(get_db), user=Depends(current_user)):
    obj = batch_service.get_batch(db, batch_no)
    return Response(
        json.dumps(obj, ensure_ascii=False, indent=2),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="receipt-{batch_no}.json"'},
    )

@router.get('/ledger/accounts')
def ledger_accounts(db=Depends(get_db), user=Depends(current_user)):
    return batch_service.list_ledger(db)

@router.get('/ledger/entries')
def ledger_entries(batch_no: str | None = None, db=Depends(get_db), user=Depends(current_user)):
    return batch_service.list_entries(db, batch_no)
