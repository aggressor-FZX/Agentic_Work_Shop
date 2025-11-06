# celery_app.py
import os
from celery import Celery

BROKER_URL = os.getenv("BROKER_URL", "amqp://agent:agentpass@localhost:5672//")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery(
    "agentic_workers",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["tasks"],
)

# Sensible defaults
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=3600,        # hard kill
    task_soft_time_limit=3300,   # graceful
    task_routes={
        "tasks.run_manager_task": {"queue": "agentic.work"},
    },
    result_expires=3600,
)
