"""Engine construction with the SQLite pragmas the spec requires.

Every new DBAPI connection runs ``foreign_keys=ON``, ``journal_mode=WAL`` and a
``busy_timeout`` so that a single-process app with a background worker never
silently loses referential integrity or fails instantly under a short lock.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url

from study_companion.config import Settings, get_settings


def _install_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


def build_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and settings.sqlite_path is not None:
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        settings.database_url,
        future=True,
        # SQLite 本地文件不存在真正的"陈旧连接"问题；关掉 pre_ping 以免它在池化连接上
        # 打开一个早于其他请求提交的读事务（WAL 快照隔离会因此漏读新行）。
        pool_pre_ping=url.get_backend_name() != "sqlite",
        connect_args={"check_same_thread": False} if url.get_backend_name() == "sqlite" else {},
    )
    if url.get_backend_name() == "sqlite":
        _install_sqlite_pragmas(engine)
    return engine


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
