# Shared image for api, worker, and beat services.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# WeasyPrint uses Pango/HarfBuzz for correct Devanagari and Telugu shaping.
# These are the Debian >=11 wheel-runtime packages from WeasyPrint's install
# guide; Noto Core supplies Noto Sans Devanagari and Noto Sans Telugu.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      fonts-noto-core \
      libharfbuzz-subset0 \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# celery is only needed by worker/beat; installed here so one image serves all three.
RUN pip install -r requirements.txt "celery[redis]>=5.4"

COPY . .

EXPOSE 8000

# Default command runs the api; worker/beat override `command` in docker-compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
