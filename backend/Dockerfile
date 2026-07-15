# Shared image for api, worker, and beat services.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps kept minimal; add build-essential only if a wheel needs compiling.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# celery is only needed by worker/beat; installed here so one image serves all three.
RUN pip install -r requirements.txt "celery[redis]>=5.4"

COPY . .

EXPOSE 8000

# Default command runs the api; worker/beat override `command` in docker-compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
