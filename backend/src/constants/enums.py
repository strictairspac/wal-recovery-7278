ROLES = ('admin', 'operator', 'viewer')
DRILL_STATUSES = ('queued', 'running', 'completed', 'failed')
# 采样站批次写入生命周期：processing=原批尚未结束（含断电残留），committed=回执与账本已一起生效
BATCH_STATUSES = ('processing', 'committed')
# 故障注入动作：提交前回滚（模拟断电）、卡在处理中（模拟回执丢失）
CRASH_ACTIONS = ('rollback', 'stuck')
