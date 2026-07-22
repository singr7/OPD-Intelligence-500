# Kokoro-82M TTS micro-service for the kiosk read-aloud (deploy/tts-kokoro).
# GPU-capable (auto-falls back to CPU); ~0.3 GB VRAM. Peer of opd-vllm/opd-stt.
#
# NOTE (doc 10 §4.1): the box's docker build DNS fix must be in place, or apt/pip
# fail to resolve inside the build container.
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

# espeak-ng: Kokoro's G2P fallback for Hindi (and other non-English phonemization).
# libsndfile1: soundfile's native backend for WAV encoding.
RUN apt-get update && apt-get install -y --no-install-recommends \
        espeak-ng libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# The Kokoro model downloads from HuggingFace on first run; persist it via a
# mounted /root/.cache/huggingface volume (see README) so restarts are instant.
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
