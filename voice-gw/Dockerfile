# voice-gw runs the shared IntakeEngine in-process (S14): the Exotel Voicebot
# websocket bridges phone audio straight into `app.intake.IntakeEngine`, with no
# network hop in the turn loop (doc 02 §5; keeps the V1<1.5s / V2<3.5s p90 budget).
# It stays a SEPARATE container from api so a telephony crash never takes HTTP
# down (doc 05 §3).
#
# The build context is the repo ROOT (see docker-compose `voice-gw.build.context`),
# not ./voice-gw, because the image needs both `backend/` and `voice-gw/`. The repo
# tree is mirrored under /app so `app.tiers.CONFIG_PATH`
# (`Path(__file__).parents[2]/config/tiers.yaml`) resolves the same as on the host.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend:/app/voice-gw

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Backend deps (engine + providers) plus voice-gw's own websocket deps.
COPY backend/requirements.txt ./backend-requirements.txt
COPY voice-gw/requirements.txt ./voice-gw-requirements.txt
RUN pip install -r backend-requirements.txt -r voice-gw-requirements.txt

# Repo layout preserved: /app/backend/app importable as `app`, /app/voice-gw/gw as
# `gw`, /app/config read by app.tiers.
COPY backend/ ./backend/
COPY config/ ./config/
COPY voice-gw/ ./voice-gw/

EXPOSE 8080

CMD ["uvicorn", "gw.main:app", "--host", "0.0.0.0", "--port", "8080"]
