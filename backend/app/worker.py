"""Celery app for the `worker` and `beat` compose services.

S1 provides only the wiring + a trivial healthcheck task so the containers have
a real entrypoint and start green. Real jobs (check-in scheduling, reminder
dispatch, outbound campaigns, usage rollups) are added in later sessions.

`celery -A app.worker:celery_app` (see docker-compose) needs a module-level
Celery *instance*, so we build one at import. Celery is only installed in the
worker/beat images and is imported lazily by `make_celery()`; importing this
module without celery installed (e.g. the api service, the test venv) is fine
as long as nothing touches `celery_app`.
"""

import os


def make_celery():
    from celery import Celery

    broker = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    app = Celery("opd", broker=broker, backend=broker)
    app.conf.update(task_track_started=True, timezone="Asia/Kolkata")

    @app.task(name="opd.ping")
    def ping() -> str:
        return "pong"

    return app


# Module-level instance the celery CLI binds to. Guarded so importing this
# module in an environment without celery (api service, pytest) does not crash;
# the worker/beat containers always have celery and get a real app.
try:
    celery_app = make_celery()
except ImportError:  # pragma: no cover - celery absent outside worker/beat
    celery_app = None
