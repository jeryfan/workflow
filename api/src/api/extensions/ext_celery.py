from typing import Any, cast
from zoneinfo import ZoneInfo

from celery import Celery
from fastapi import FastAPI

from api.configs import app_config


def _worker_log_format() -> str:
    return app_config.LOG_FORMAT.replace("%(trace_id)s - ", "").replace("%(trace_id)s", "")


def init_app(app: FastAPI) -> Celery:
    celery_app = cast(
        Any,
        Celery(
            app.title,
            broker=app_config.CELERY_BROKER_URL,
            backend=app_config.CELERY_BROKER_URL,
        ),
    )

    celery_app.conf.update(
        imports=("api.tasks",),
        beat_schedule={
            "dispatch-workflow-schedules": {
                "task": "workflow.dispatch_schedules",
                "schedule": 5.0,
            }
        },
        broker_connection_retry_on_startup=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        task_time_limit=app_config.TASK_TIME_LIMIT_SECONDS,
        task_soft_time_limit=app_config.TASK_SOFT_TIME_LIMIT_SECONDS,
        worker_prefetch_multiplier=1,
        worker_log_format=_worker_log_format(),
        worker_task_log_format=_worker_log_format(),
        worker_hijack_root_logger=False,
        timezone=ZoneInfo(app_config.LOG_TZ or "UTC"),
    )

    if app_config.LOG_FILE:
        celery_app.conf.update(worker_logfile=app_config.LOG_FILE)

    celery_app.set_default()
    app.state.celery = celery_app
    return celery_app
