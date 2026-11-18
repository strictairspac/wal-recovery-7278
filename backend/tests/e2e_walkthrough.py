"""端到端剧本：模拟采样站一天的批次重试与断电恢复，人工可读输出。"""
from fastapi.testclient import TestClient

from src.main import app
from src.config.database import Base, engine, Session
from src.models.entities import User, LedgerAccount
from src.utils.security import hash_password, token

Base.metadata.create_all(engine)
with Session() as db:
    if not db.query(User).filter_by(email='operator@haili.local').first():
        db.add(User(email='operator@haili.local', role='operator',
                    password_hash=hash_password('Recovery2026!')))
    for key, balance in [('sample_A', 100), ('sample_B', 50)]:
        if not db.query(LedgerAccount).filter_by(key=key).first():
            db.add(LedgerAccount(key=key, balance=balance))
    db.commit()

with Session() as db:
    user = db.query(User).filter_by(email='operator@haili.local').one()
    headers = {'Authorization': f'Bearer {token(user)}'}

c = TestClient(app)
BATCH = 'BATCH-E2E-01'
payload = {'batch_no': BATCH, 'station': '海砾一号站',
           'items': [{'key': 'sample_A', 'delta': 20}, {'key': 'sample_B', 'delta': -10}]}

print('== 1. 首次提交 ==')
r = c.post('/batches', json=payload, headers=headers)
print(r.status_code, r.json()['receipt']['receipt_no'], 'replay=', r.json()['replay'])

print('== 2. 收不到回执，同批次号同内容重发 ==')
r = c.post('/batches', json=payload, headers=headers)
print(r.status_code, 'replay=', r.json()['replay'], 'receipt_no=', r.json()['receipt']['receipt_no'])

print('== 3. 同批次号换内容 -> 必须拒绝 ==')
r = c.post('/batches', json={**payload, 'items': [{'key': 'sample_A', 'delta': 999}]}, headers=headers)
print(r.status_code, r.json()['detail'])

print('== 4. 新批次：提交前断电 ==')
BATCH2 = 'BATCH-E2E-02'
p2 = {**payload, 'batch_no': BATCH2}
r = c.post('/batches/crash-demo', json={**p2, 'action': 'rollback'}, headers=headers)
print(r.status_code, r.json()['detail'])
print('   残留状态:', c.get('/batches/' + BATCH2, headers=headers).json()['status'],
      '流水数:', len(c.get(f'/ledger/entries?batch_no={BATCH2}', headers=headers).json()))

print('== 5. 恢复后同号同内容重试，应成功入账不丢数据 ==')
r = c.post('/batches', json=p2, headers=headers)
print(r.status_code, 'replay=', r.json()['replay'], 'attempts=', r.json()['batch']['attempts'])

print('== 6. 回执丢失（已提交）后重发，取回原回执 ==')
BATCH3 = 'BATCH-E2E-03'
p3 = {**payload, 'batch_no': BATCH3}
r = c.post('/batches/crash-demo', json={**p3, 'action': 'stuck'}, headers=headers)
print(r.status_code, r.json()['detail'])
r = c.post('/batches', json=p3, headers=headers)
print(r.status_code, 'replay=', r.json()['replay'], 'receipt_no=', r.json()['receipt']['receipt_no'])

print('== 7. 核对回执与账本 ==')
accounts = {a['key']: a['balance'] for a in c.get('/ledger/accounts', headers=headers).json()}
print('账本:', accounts)
# 初始 A100/B50；批1 +20/-10；批2 +20/-10；批3 +20/-10（重放不重复）
assert accounts == {'sample_A': 160, 'sample_B': 20}, accounts
for b in (BATCH, BATCH2, BATCH3):
    detail = c.get('/batches/' + b, headers=headers).json()
    entries = c.get(f'/ledger/entries?batch_no={b}', headers=headers).json()
    assert detail['status'] == 'committed' and len(entries) == 2
    # 回执里的 balance_after 必须与流水一致
    for rc, en in zip(sorted(detail['receipt']['entries'], key=lambda x: x['key']),
                      sorted(entries, key=lambda x: x['key'])):
        assert rc['balance_after'] == en['balance_after']
    print(f'  {b}: 回执/流水一致，每条批次恰好2条流水')

print('== 8. 原恢复演练下载不受影响 ==')
assert any(rt.path == '/recoveries/{ident}/export' for rt in app.routes)
print('  /recoveries/{ident}/export 路由仍在')
print('ALL E2E CHECKS PASSED')
