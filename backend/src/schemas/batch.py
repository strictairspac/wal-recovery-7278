from typing import Literal
from pydantic import BaseModel, Field

class BatchItem(BaseModel):
    key: str = Field(min_length=1, max_length=50, pattern=r'^[A-Za-z0-9_-]+$')
    delta: int = Field(ge=-1000000000, le=1000000000)

class BatchWriteInput(BaseModel):
    # 采样站分配的同一批次号；收不到回执就用它重发整批
    batch_no: str = Field(min_length=2, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    station: str = Field(default='采样站', min_length=1, max_length=80)
    items: list[BatchItem] = Field(min_length=1, max_length=30)

class CrashDemoInput(BatchWriteInput):
    # rollback=提交前断电（整批回滚，重试可重新入账）；stuck=回执丢失但已提交（重试只回原回执）
    action: Literal['rollback', 'stuck']
