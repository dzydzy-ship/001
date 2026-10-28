"""数据库连接与会话管理。MVP 使用 SQLite，SQLAlchemy 模型层与 PostgreSQL 兼容。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

try:  # 测试替身环境（tests/_stubs.py）里没有 event，真库有
    from sqlalchemy import event as _sa_event
except ImportError:  # pragma: no cover
    _sa_event = None

from .config import DATA_DIR, settings

DATA_DIR.mkdir(parents=True, exist_ok=True)

_is_sqlite = settings.database_url.startswith("sqlite")
# timeout：写锁竞争时最多等 30 秒（默认 5 秒，采集与定时任务并发时容易「database is locked」）
connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

if _is_sqlite and _sa_event is not None:

    @_sa_event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 连接级初始化
        """WAL 让读不阻塞写、busy_timeout 让写等待而不是立刻报错。"""
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


def get_db():
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等），并给旧库补新增列。"""
    from . import models  # noqa: F401  确保模型被注册

    Base.metadata.create_all(bind=engine)
    _ensure_product_tags()


def _ensure_product_tags() -> None:
    """给旧库补 products.tags 列（create_all 只会建新表、不会改已有表结构）。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "products" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("products")}
    if "tags" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE products ADD COLUMN tags JSON"))
