from __future__ import annotations

from celery import Celery

from api.config import get_settings

settings = get_settings()
celery_app = Celery("workflow", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    imports=("api.tasks",),
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_time_limit=settings.task_time_limit_seconds,
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    beat_schedule={
        "dispatch-workflow-schedules": {
            "task": "workflow.dispatch_schedules",
            "schedule": 5.0,
        }
    },
)
