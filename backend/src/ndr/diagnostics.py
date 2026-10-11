"""Content-free, bounded local task diagnostics (never arbitrary exception text)."""

import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("ndr.diagnostics")


def start_diagnostics(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "tasks.log", maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def stop_diagnostics(handler):
    logger.removeHandler(handler)
    handler.close()


def log_failure(operation, job_id, error):
    from .storage.transactions import database_error_detail

    locations = [(Path(frame.filename).name, frame.lineno, frame.name)
                 for frame in traceback.extract_tb(error.__traceback__)]
    logger.error("task_failure job=%s operation=%s error=%s locations=%s",
                 job_id, operation, database_error_detail(error), locations)
