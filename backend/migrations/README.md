# 初始化结构
SQLAlchemy metadata.create_all首次建users、log_sets、crash_cases、recoveries、audits。log_sets.name、crash_cases.name唯一，关联使用外键，入队冻结日志和崩溃点快照。暂无在线schema升级器。

## 采样站幂等批次（第二批表）
- ledger_accounts：采样账本账户，key 唯一，balance 为大整数；余额由已提交批次流水累积。
- batch_writes：批次号 batch_no 唯一，content_hash 绑定批次内容；status=processing/committed，receipt 与账本在同一事务提交；processing 残留行不含任何 ledger_entries，重试可接管。
- ledger_entries：入账流水，(batch_id,key) 唯一约束兜底防重复入账，记录 delta 与 balance_after。
