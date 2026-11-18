from datetime import datetime, timezone
from sqlalchemy import String, Integer, BigInteger, JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base

def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
class User(Base):
    __tablename__='users'
    id: Mapped[int]=mapped_column(primary_key=True)
    email: Mapped[str]=mapped_column(String(120),unique=True)
    role: Mapped[str]=mapped_column(String(20))
    password_hash: Mapped[str]=mapped_column(String(250))
class LogSet(Base):
    __tablename__='log_sets'
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(100),unique=True)
    initial: Mapped[dict]=mapped_column(JSON)
    records: Mapped[list]=mapped_column(JSON)
    description: Mapped[str]=mapped_column(String(500))
class CrashCase(Base):
    __tablename__='crash_cases'
    id: Mapped[int]=mapped_column(primary_key=True)
    log_set_id: Mapped[int]=mapped_column(ForeignKey('log_sets.id'))
    name: Mapped[str]=mapped_column(String(100),unique=True)
    crash_after: Mapped[int]=mapped_column(Integer)
class Recovery(Base):
    __tablename__='recoveries'
    id: Mapped[int]=mapped_column(primary_key=True)
    crash_case_id: Mapped[int]=mapped_column(ForeignKey('crash_cases.id'))
    label: Mapped[str]=mapped_column(String(120))
    status: Mapped[str]=mapped_column(String(20),default='queued')
    snapshot: Mapped[dict]=mapped_column(JSON)
    result: Mapped[dict | None]=mapped_column(JSON,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class Audit(Base):
    __tablename__='audits'
    id: Mapped[int]=mapped_column(primary_key=True)
    actor: Mapped[str]=mapped_column(String(120))
    action: Mapped[str]=mapped_column(String(120))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class LedgerAccount(Base):
    """采样账本账户。余额由已提交批次的入账流水累积，余额非负。"""
    __tablename__='ledger_accounts'
    id: Mapped[int]=mapped_column(primary_key=True)
    key: Mapped[str]=mapped_column(String(50),unique=True)
    balance: Mapped[int]=mapped_column(BigInteger,default=0)
class BatchWrite(Base):
    """采样站整批写入。batch_no 唯一；回执与账本流水在同一事务内提交。"""
    __tablename__='batch_writes'
    id: Mapped[int]=mapped_column(primary_key=True)
    batch_no: Mapped[str]=mapped_column(String(80),unique=True)
    station: Mapped[str]=mapped_column(String(80))
    content_hash: Mapped[str]=mapped_column(String(64))
    payload: Mapped[list]=mapped_column(JSON)
    status: Mapped[str]=mapped_column(String(20),default='processing')
    attempts: Mapped[int]=mapped_column(Integer,default=0)
    receipt: Mapped[dict | None]=mapped_column(JSON,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
    committed_at: Mapped[datetime | None]=mapped_column(DateTime,nullable=True)
class LedgerEntry(Base):
    """批次入账流水。仅在批次提交事务内写入，因此不会出现孤儿/重复入账。"""
    __tablename__='ledger_entries'
    id: Mapped[int]=mapped_column(primary_key=True)
    batch_id: Mapped[int]=mapped_column(ForeignKey('batch_writes.id'))
    batch_no: Mapped[str]=mapped_column(String(80),index=True)
    key: Mapped[str]=mapped_column(String(50),index=True)
    delta: Mapped[int]=mapped_column(BigInteger)
    balance_after: Mapped[int]=mapped_column(BigInteger)
    __table_args__=(UniqueConstraint('batch_id','key',name='uq_ledger_entry_batch_key'),)
