import logging
import logging.config
import os
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from api.configs import app_config


def init_app(app: FastAPI) -> None:
    handlers: dict[str, dict[str, Any]] = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "default",
            "filters": ["trace_id"],
        },
    }

    log_file = app_config.LOG_FILE
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": log_file,
            "maxBytes": app_config.LOG_FILE_MAX_SIZE * 1024 * 1024,
            "backupCount": app_config.LOG_FILE_BACKUP_COUNT,
            "encoding": "utf-8",
            "formatter": "default",
            "filters": ["trace_id"],
        }

    handler_names = list(handlers)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"trace_id": {"()": "api.libs.log_context.TraceIdFilter"}},
            "formatters": {
                "default": {
                    "format": app_config.LOG_FORMAT,
                    "datefmt": app_config.LOG_DATEFORMAT,
                }
            },
            "handlers": handlers,
            "root": {"level": app_config.LOG_LEVEL, "handlers": handler_names},
            "loggers": {
                "uvicorn": {
                    "handlers": handler_names,
                    "level": app_config.LOG_LEVEL,
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": handler_names,
                    "level": "INFO",
                    "propagate": False,
                },
                "sqlalchemy.engine": {"level": "WARNING"},
            },
        }
    )
    _apply_timezone()


def _apply_timezone() -> None:
    log_tz = app_config.LOG_TZ
    if not log_tz:
        return

    timezone = ZoneInfo(log_tz)

    def time_converter(seconds: float | None) -> time.struct_time:
        if seconds is None:
            return datetime.now(tz=timezone).timetuple()
        return datetime.fromtimestamp(seconds, tz=timezone).timetuple()

    formatters = {handler.formatter for handler in logging.getLogger().handlers if handler.formatter}
    for formatter in formatters:
        formatter.converter = time_converter
