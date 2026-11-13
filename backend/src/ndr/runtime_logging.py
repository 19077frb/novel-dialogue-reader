"""Configure server console diagnostics without reconfiguring application handlers."""

from __future__ import annotations

import json
import logging

from .diagnostics import error_summary, log_event

STARTUP_FAILURE_MESSAGES = {
    "invalid_port": "端口必须在 1 到 65535 之间，请检查 --port 或 NDR_PORT。",
    "invalid_run_seconds": "限时运行秒数必须大于 0，请检查 --run-seconds。",
    "missing_pages": "程序的页面文件缺失，请重新下载并完整解压免安装包。",
    "port_in_use": "端口被占用，请关闭原服务，或使用 --port 8800。",
    "library_in_use": "这份书库正在运行，请使用已打开的阅读器，并等待该实例启动完成。",
    "newer_library": "书库版本比当前程序更新，请使用新版程序，不要降级。",
    "startup_timeout": "启动超时，请检查端口和数据目录的空间、写入权限。",
    "server_not_started": "服务未能启动，请检查端口和数据目录的空间、写入权限。",
}


class StartupError(RuntimeError):
    """A fixed launcher diagnosis whose text contains no external error content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(STARTUP_FAILURE_MESSAGES[code])


class StartupArgumentError(StartupError, ValueError):
    pass


def startup_failure_message(error: Exception, *, diagnostics_available: bool, stage: str) -> str:
    if isinstance(error, StartupError):
        reason = STARTUP_FAILURE_MESSAGES[error.code]
    elif stage == "configuration":
        reason = "配置读取失败，请检查 NDR_ 环境配置和已保存的应用设置。"
    elif stage == "library_lock":
        reason = "无法锁定书库，请检查数据目录的写入权限，并确认书库是否正在运行。"
    else:
        reason = "服务启动失败，请检查数据目录的空间、写入权限与程序文件。"
    diagnostic_hint = ("请查看数据目录中的 logs/tasks.log。" if diagnostics_available
                       else "本次未初始化诊断日志。")
    return f"启动失败。{reason}{diagnostic_hint}"


class SafeServerFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if record.levelno < logging.WARNING:
            return super().format(record)
        error = record.exc_info[1] if record.exc_info else None
        if error is None and isinstance(record.msg, BaseException):
            error = record.msg
        # ASGI lifespan failures can be raw traceback strings with no exc_info.
        # Their lifecycle boundary already records the underlying exception.
        log_event("server_error", level=record.levelno, error=error,
                  stage="server", deduplicate=True)
        summary = {"event": "server_error"}
        if error is not None:
            summary["error"] = error_summary(error)
        return f"{record.levelname} {json.dumps(summary, ensure_ascii=False)}"


def configure_server_logging() -> None:
    """Only replace Uvicorn's console sink, leaving diagnostic files attached."""
    handler = logging.StreamHandler()
    handler.setFormatter(SafeServerFormatter("%(levelname)s %(message)s"))
    server_logger = logging.getLogger("uvicorn.error")
    for previous in server_logger.handlers:
        if isinstance(previous.formatter, SafeServerFormatter):
            previous.close()
    server_logger.handlers = [handler]
    server_logger.setLevel(logging.INFO)
    server_logger.propagate = False
