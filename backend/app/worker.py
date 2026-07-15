"""Celery app skeleton for the `worker` and `beat` compose services.

S1 provides only the wiring + a trivial healthcheck task so the containers have
a real entrypoint and start green. Real jobs (check-in scheduling, reminder
dispatch, outbound campaigns, usage rollups) are added in later sessions.

Celery is imported lazily so the api service and the test suite don't need the
broker installed; the worker/beat containers install it from requirements.
"""

import os


def make_celery():  # pragma: no cover - exercised only in the worker container
    from celery import Celery

    broker = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    app = Celery("opd", broker=broker, backend=broker)
    app.conf.update(task_track_started=True, timezone="Asia/Kolkata")

    @app.task(name="opd.ping")
    def ping() -> str:
        return "pong"

    return app
