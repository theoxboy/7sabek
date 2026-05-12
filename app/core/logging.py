import logging
import os
from pathlib import Path

from app.core.config import get_settings

LOG_FILE_PATH = os.getenv("BACKEND_LOG_PATH", "logs/backend.log")


def configure_logging() -> None:
    settings = get_settings()
    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    log_path = Path(LOG_FILE_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if not _has_file_handler(root_logger, log_path):
        root_logger.addHandler(file_handler)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if not _has_file_handler(logger, log_path):
            logger.addHandler(file_handler)


def _has_file_handler(logger: logging.Logger, log_path: Path) -> bool:
    log_path = log_path.resolve()
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            if Path(handler.baseFilename).resolve() == log_path:
                return True
    return False
