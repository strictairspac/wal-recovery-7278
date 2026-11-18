"""同批次号串行化：Redis 分布式锁优先，Redis 不可用时进程内线程锁兜底。

并发只认一份提交：同一 batch_no 的多个请求只会有一个进入临界区，
其余等待后直接读到已提交回执（幂等返回）或处理中状态。
"""
import os
import threading
import contextlib
from redis import Redis
from src.config.settings import REDIS_URL

_redis = Redis.from_url(REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
_local_lock = threading.Lock()
_local_guards: dict[str, threading.Lock] = {}

@contextlib.contextmanager
def batch_guard(batch_no: str):
    # LOCK_BACKEND=local 用于离线测试/单机开发；生产默认走 Redis 分布式锁
    if os.environ.get('LOCK_BACKEND') == 'local':
        with _local_lock:
            guard = _local_guards.setdefault(batch_no, threading.Lock())
        with guard:
            yield
        return
    name = f'batch-write:{batch_no}'
    try:
        lock = _redis.lock(name, timeout=20, blocking_timeout=25)
        acquired = lock.acquire(blocking=True)
        if not acquired:
            raise RuntimeError('批次处理繁忙，请稍后重试')
        try:
            yield
        finally:
            try:
                lock.release()
            except Exception:
                # 锁可能已超时自动释放；临界区内的数据库事务本身具备原子性
                pass
    except RuntimeError:
        raise
    except Exception:
        # Redis 故障时退化为单进程互斥，保证单 worker 部署下仍然串行
        with _local_lock:
            guard = _local_guards.setdefault(batch_no, threading.Lock())
        with guard:
            yield
