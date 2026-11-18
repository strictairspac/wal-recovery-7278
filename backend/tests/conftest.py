import os
import sys
import pathlib

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 必须在导入应用模块前注入环境变量
os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/15')  # 本机无 Redis，走进程锁兜底
os.environ.setdefault('LOCK_BACKEND', 'local')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from src.config import database as db_module  # noqa: E402
from src.config.database import Base  # noqa: E402
from src.models import entities  # noqa: E402,F401  确保所有表已注册
from src.main import app  # noqa: E402
from src.utils.security import hash_password, token  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def session_factory(tmp_path):
    # 文件库 + WAL + 普通连接池：让并发线程持有独立连接，贴近生产 PG 的连接模型
    engine = create_engine(
        f'sqlite:///{tmp_path / "test.db"}',
        connect_args={'check_same_thread': False, 'timeout': 30},
        poolclass=QueuePool,
    )

    @event.listens_for(engine, 'connect')
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=30000')
        cur.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db_module.engine = engine
    db_module.Session = factory
    yield factory
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    # get_db 依赖在函数导入时绑定了 src.config.database.Session 名字，需要补丁
    from src.config import database as db_conf

    def override_get_db():
        with db_conf.Session() as session:
            yield session

    app.dependency_overrides[db_conf.get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login(client, session_factory, role='operator'):
    email = f'{role}@haili.local'
    with session_factory() as db:
        from src.models.entities import User
        db.add(User(email=email, role=role, password_hash=hash_password('pw')))
        db.commit()
        user = db.query(User).filter_by(email=email).one()
        return token(user)


@pytest.fixture()
def auth_headers(client, session_factory):
    tk = _login(client, session_factory, 'operator')
    return {'Authorization': f'Bearer {tk}'}


@pytest.fixture()
def admin_headers(client, session_factory):
    tk = _login(client, session_factory, 'admin')
    return {'Authorization': f'Bearer {tk}'}


@pytest.fixture()
def viewer_headers(client, session_factory):
    tk = _login(client, session_factory, 'viewer')
    return {'Authorization': f'Bearer {tk}'}
