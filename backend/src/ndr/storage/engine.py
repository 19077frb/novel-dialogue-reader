"""数据库引擎、会话工厂与迁移状态。

SQLite 连接统一启用外键约束、busy_timeout 与 WAL；应用不通过 cwd 猜测数据库位置。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..domain.enums import DatabaseState


def create_db_engine(settings: Settings) -> Engine:
    """建立 SQLite 引擎并安装连接级 PRAGMA。"""

    settings.ensure_data_dir()
    engine = create_engine(
        settings.database_url,
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@dataclass(frozen=True)
class MigrationStatus:
    state: DatabaseState
    revision: str | None = None
    head_revision: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"state": self.state.value}
        if self.revision:
            payload["revision"] = self.revision
        if self.head_revision:
            payload["head_revision"] = self.head_revision
        if self.detail:
            payload["detail"] = self.detail
        return payload


@lru_cache(maxsize=1)
def head_revision() -> str | None:
    """仓库内 alembic 迁移的 head；读取失败时返回 None，不伪造版本号。"""

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        ini = Settings().alembic_ini_path
        if not ini.exists():
            return None
        script = ScriptDirectory.from_config(Config(str(ini)))
        return script.get_current_head()
    except Exception:  # pragma: no cover - 迁移文件缺失或损坏时不掩盖真实状态
        return None


def migration_status(engine: Engine) -> MigrationStatus:
    """不做破坏性操作地报告数据库迁移状态。"""

    head = head_revision()
    try:
        tables = set(inspect(engine).get_table_names())
    except Exception as exc:  # pragma: no cover - 仅在数据库文件不可读时触发
        return MigrationStatus(
            state=DatabaseState.ERROR,
            head_revision=head,
            detail=f"无法读取数据库结构：{exc}",
        )

    if "alembic_version" not in tables:
        return MigrationStatus(
            state=DatabaseState.NOT_INITIALIZED,
            head_revision=head,
            detail="数据库尚未迁移：请运行 alembic -c backend/alembic.ini upgrade head",
        )

    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()

    if head is not None and revision != head:
        return MigrationStatus(
            state=DatabaseState.OUTDATED,
            revision=revision,
            head_revision=head,
            detail="数据库版本落后于仓库迁移，请运行 alembic upgrade head",
        )

    return MigrationStatus(state=DatabaseState.READY, revision=revision, head_revision=head)
