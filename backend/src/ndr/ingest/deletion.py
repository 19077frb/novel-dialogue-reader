"""删除指定书籍的数据库记录，将独占文件移入数据目录回收区。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from ..api.errors import ApiError
from ..config import Settings
from ..domain.enums import ErrorCode, JobState
from ..storage.base import new_id
from ..storage.models import (
    Annotation,
    Book,
    BookVersion,
    Correction,
    ExportArtifact,
    ExportSnapshot,
    Gap,
    Job,
    Quote,
    Scene,
)
from ..storage.paths import resolve_within
from ..storage.transactions import transaction
from .query import get_book_or_404


def delete_book(
    session_factory: sessionmaker[Session], settings: Settings, book_id: str,
) -> None:
    moved: list[tuple[Path, Path]] = []
    try:
        with transaction(session_factory) as session:
            # 与任务创建串行化，避免检查后又有任务进入该书籍。
            session.execute(text("BEGIN IMMEDIATE"))
            get_book_or_404(session, book_id)
            active = session.execute(select(Job.id).where(
                Job.book_id == book_id,
                Job.state.in_((JobState.QUEUED, JobState.RUNNING, JobState.PAUSING)),
            )).first()
            if active:
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR,
                    "本书仍有任务正在运行或排队，请先停止任务并等待结束后再删除",
                    details={"book_id": book_id, "job_id": active[0]},
                    status_code=409,
                )

            versions = select(BookVersion.id).where(BookVersion.book_id == book_id)
            quotes = select(Quote.id).where(Quote.book_version_id.in_(versions))
            targets = (
                quotes,
                select(Gap.id).where(Gap.book_version_id.in_(versions)),
                select(Scene.id).where(Scene.book_version_id.in_(versions)),
                select(Annotation.id).where(Annotation.quote_id.in_(quotes)),
            )
            # 更正记录的目标是逻辑引用，不会由外键自动清理。
            for target_ids in targets:
                session.execute(delete(Correction).where(Correction.target_id.in_(target_ids)))

            artifact_ids = list(session.execute(select(ExportArtifact.id).join(
                ExportSnapshot, ExportArtifact.snapshot_id == ExportSnapshot.id,
            ).where(ExportSnapshot.book_id == book_id)).scalars())
            # 先完成可能耗时的数据库清理，再移动文件并立即提交。
            # WAL 读者在事务提交前仍能看见旧记录，必须尽量保持正文可读。
            session.execute(delete(Book).where(Book.id == book_id))
            candidates = [("books", book_id), *(("exports", item) for item in artifact_ids)]
            trash = Path("trash") / f"{book_id}-{new_id()}"
            for category, target_id in candidates:
                # 仅操作独占的精确 ID 目录；禁止目录分隔符或符号链接重定向。
                if Path(target_id).name != target_id or target_id in {".", ".."}:
                    raise ApiError.validation("书籍文件目录无效")
                source = resolve_within(settings, Path(category) / target_id)
                expected = settings.data_dir.resolve() / category / target_id
                if source != expected:
                    raise ApiError.validation("书籍文件目录包含重定向，无法删除")
                if not source.exists():
                    continue
                destination = resolve_within(settings, trash / category / target_id)
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)
                moved.append((source, destination))

    except Exception:
        # 文件移动失败或事务回滚时，恢复已移动文件，确保仍可阅读原书。
        for source, destination in reversed(moved):
            destination.rename(source)
        raise
