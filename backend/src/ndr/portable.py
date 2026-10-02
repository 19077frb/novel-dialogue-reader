"""Windows portable launcher; bundled assets and writable user data stay separate."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import uuid
import webbrowser
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from . import __version__
from .config import REPO_ROOT, Settings, get_settings


class LibraryInUseError(RuntimeError):
    pass


class LibraryLock:
    """OS-released lock, not a stale PID file; also guards migrations and backups."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.file = (data_dir / "portable.lock").open("a+b")
        self.locked = False

    def __enter__(self) -> LibraryLock:
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0, 2)
                if self.file.tell() == 0:
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise LibraryInUseError("这份书库正在运行，请使用已打开的阅读器。") from exc
        self.locked = True
        return self

    def __exit__(self, *args: object) -> None:
        if self.locked and os.name == "nt":
            import msvcrt

            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()
        self.locked = False


def portable_settings(*, data_dir: Path | None, port: int | None) -> Settings:
    # Do not consume an arbitrary .env next to the EXE or in the launch directory.
    options: dict[str, object] = {
        "host": "127.0.0.1",
        "static_dir": REPO_ROOT / "frontend" / "dist",
        "auto_migrate": False,
    }
    if data_dir is not None:
        options["data_dir"] = data_dir
    if port is not None:
        options["port"] = port
    settings = Settings(_env_file=None, **options)
    if not 1 <= settings.port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间。")
    return settings


def backup_before_upgrade(settings: Settings) -> Path | None:
    """Online SQLite backup preserves WAL changes; never downgrade a newer library."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError

    if not settings.database_path.exists():
        return None
    scripts = ScriptDirectory.from_config(Config(str(settings.alembic_ini_path)))
    with closing(sqlite3.connect(settings.database_path)) as source:
        has_version = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        revisions = (
            [row[0] for row in source.execute("SELECT version_num FROM alembic_version")]
            if has_version
            else []
        )
        if revisions == [scripts.get_current_head()]:
            return None
        for revision in revisions:
            try:
                known = scripts.get_revision(revision)
            except CommandError as exc:
                raise RuntimeError("书库版本比当前程序更新，请使用新版程序，不要降级。") from exc
            if known is None:
                raise RuntimeError("书库版本比当前程序更新，请使用新版程序，不要降级。")
        destination = settings.data_dir / "backups"
        destination.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = destination / f"before-upgrade-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        with closing(sqlite3.connect(path)) as target:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
    return path


def running_url(data_dir: Path) -> str | None:
    """Trust only a locked library's validated loopback service, never arbitrary URLs."""
    try:
        info = json.loads((data_dir / "portable-server.json").read_text(encoding="utf-8"))
        port = info["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        url = f"http://127.0.0.1:{port}"
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"{url}/api/health", timeout=1) as response:
            health = json.load(response)
        if health.get("app") == "novel-dialogue-reader" and health.get("status") == "ok":
            return url
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def launch(settings: Settings, *, no_browser: bool, run_seconds: float | None) -> None:
    import uvicorn

    from .storage.migrate import run_migrations

    if settings.static_dir is None or not (settings.static_dir / "index.html").is_file():
        raise RuntimeError("程序的页面文件缺失，请重新下载并完整解压免安装包。")
    status_file = settings.data_dir / "portable-server.json"
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    done = threading.Event()
    watcher: threading.Thread | None = None
    errors: list[Exception] = []
    try:
        # Reserve before any DB write; do not reuse/terminate strangers on this port.
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((settings.host, settings.port))
        except OSError as exc:
            raise RuntimeError(
                f"端口 {settings.port} 被占用。请先关闭原服务，或使用 --port 8800。"
            ) from exc
        backup = backup_before_upgrade(settings)
        if backup:
            print(f"升级前数据库备份：{backup}")
        run_migrations(settings)
        # Configure before importing the module-level application.
        for name, value in (
            ("NDR_DATA_DIR", str(settings.data_dir)),
            ("NDR_STATIC_DIR", str(settings.static_dir)),
            ("NDR_HOST", settings.host),
            ("NDR_PORT", str(settings.port)),
        ):
            os.environ[name] = value
        get_settings.cache_clear()
        from .app import create_app

        app = create_app(settings)
        server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port))

        def on_ready() -> None:
            try:
                deadline = time.monotonic() + 60
                while not server.started:
                    if done.wait(0.1):
                        return
                    if time.monotonic() >= deadline:
                        raise RuntimeError("启动超时，请查看控制台的错误信息。")
                status_file.write_text(json.dumps({"port": settings.port}), encoding="utf-8")
                url = f"http://127.0.0.1:{settings.port}"
                print(f"阅读器已启动：{url}\n数据目录：{settings.data_dir}\n按 Ctrl+C 停止服务。")
                if not no_browser and not webbrowser.open(url):
                    print("未能自动打开浏览器，请打开上方地址。")
                if run_seconds is not None and not done.wait(run_seconds):
                    server.should_exit = True
            except Exception as exc:
                errors.append(exc)
                server.should_exit = True

        watcher = threading.Thread(target=on_ready, daemon=True)
        watcher.start()
        server.run(sockets=[listener])
        if errors:
            raise errors[0]
        if not server.started:
            raise RuntimeError("服务未能启动，请查看控制台的错误信息。")
    finally:
        done.set()
        if watcher is not None:
            watcher.join(timeout=2)
        listener.close()
        status_file.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="轻小说对话辅助阅读器（免安装版）")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, help="指定书库目录，默认使用用户数据目录")
    parser.add_argument("--port", type=int, help="服务端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--check-runtime", action="store_true", help="验收用：检查内置运行环境")
    parser.add_argument("--run-seconds", type=float, help="验收用：就绪后限时运行并正常退出")
    args = parser.parse_args()
    try:
        if args.check_runtime:
            from .llm.credentials import SystemCredentialStore

            print(json.dumps({
                "version": __version__,
                "system_credentials_available": SystemCredentialStore().available,
                "frozen": bool(getattr(sys, "frozen", False)),
            }))
            return 0
        if args.run_seconds is not None and args.run_seconds <= 0:
            raise ValueError("限时运行秒数必须大于 0。")
        settings = portable_settings(data_dir=args.data_dir, port=args.port)
        try:
            with LibraryLock(settings.data_dir):
                launch(settings, no_browser=args.no_browser, run_seconds=args.run_seconds)
        except LibraryInUseError:
            for _ in range(20):
                url = running_url(settings.data_dir)
                if url:
                    print(f"阅读器已经启动：{url}")
                    if not args.no_browser:
                        webbrowser.open(url)
                    return 0
                time.sleep(0.1)
            raise
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        if not args.no_browser and sys.stdin.isatty():
            input("按回车关闭窗口…")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
