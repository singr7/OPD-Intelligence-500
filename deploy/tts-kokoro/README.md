# opd-tts — Kokoro TTS container (the "default voice now" path)

A small FastAPI service that runs **Kokoro-82M** and answers the exact contract our
`LocalTTSProvider` already calls (`POST /tts → {"audio": "<base64 wav>"}`,
`TTS_PROVIDER=local_tts`). It gives the kiosk a natural on-box voice **today**, with
a bundled default voice and **no cloning required**.

It runs as a **standalone container** on the `opd_default` network — a peer of
`opd-vllm` / `opd-stt`, managed with `docker run` (not compose), exactly like them
(doc 10 §2). The Voicebox / cloned-Dhara path is a later iteration and is untouched
by this.

Run everything below **on `omen`**, from the repo root (`~/projects/opd`).

---

## Step 1 — build the image

```bash
docker build -t opd-tts:latest deploy/tts-kokoro
```
If `apt`/`pip` can't resolve during the build, it's the docker-build DNS gotcha —
apply the `/etc/docker/daemon.json` fix in doc 10 §4.1, then rebuild.

## Step 2 — run it (GPU, on the app network, cache persisted)

```bash
docker run -d --name opd-tts \
  --gpus all \
  --network opd_default \
  --restart unless-stopped \
  -p 18020:8000 \
  -v /opt/opd/hf:/root/.cache/huggingface \
  opd-tts:latest
```
- `--network opd_default` → the api reaches it by name at `http://opd-tts:8000`.
- `-p 18020:8000` → host port for your own curl tests (the api does **not** use this).
- The HF cache is shared with the other models; first run downloads Kokoro (~330 MB),
  then restarts are instant.
- No GPU? Drop `--gpus all` — Kokoro is fast enough on CPU for short kiosk prompts.

Watch it come up:
```bash
docker logs -f --tail=50 opd-tts     # look for "starting on cuda" + uvicorn ready
```

## Step 3 — smoke-test the voice (before touching the app)

```bash
# health + the language defaults it will use
curl -s localhost:18020/health
curl -s localhost:18020/voices

# synthesize a Hindi line and write a wav you can play
curl -s -X POST localhost:18020/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"नमस्ते, आप कैसे हैं?","language":"hi","sample_rate":24000}' \
| python3 -c 'import sys,json,base64; open("/tmp/dhara.wav","wb").write(base64.b64decode(json.load(sys.stdin)["audio"])); print("wrote /tmp/dhara.wav")'

aplay /tmp/dhara.wav      # or scp /tmp/dhara.wav back to your Mac to listen
```
Try `"language":"en"` too. If a voice sounds wrong, `GET /voices` shows the default
ids; any valid Kokoro voice id for that language can be set as `LOCAL_TTS_VOICE`.

## Step 4 — point the app at it (`~/projects/opd/.env`)

```
TTS_PROVIDER=local_tts
LOCAL_TTS_URL=http://opd-tts:8000
LOCAL_TTS_VOICE=                 # blank = per-language default (hf_alpha / af_heart)
NEXT_PUBLIC_KIOSK_SERVER_TTS=1
```

## Step 5 — rebuild the app so the kiosk uses it

```bash
docker compose up -d --build api web
```
The **web rebuild is required** — `NEXT_PUBLIC_KIOSK_SERVER_TTS` is baked in at build
time. `api` picks up the new `.env` on recreate.

## Step 6 — verify end-to-end

```bash
# the kiosk read-aloud route now returns Kokoro audio
curl -s -X POST localhost:18080/kiosk/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"आपका टोकन नंबर बयालीस है","lang":"hi"}' | python3 -m json.tool | head

# usage_events should show provider=local-tts
docker compose exec postgres psql -U opd -d opd -c \
  "select provider,count(*) from usage_events where provider='local-tts' group by provider;"
```
Then open `/kiosk` and confirm a question is read aloud in the Kokoro voice; turn the
flag off (or take `opd-tts` down) and confirm it falls back to the browser voice.

---

## Ops (same shape as opd-vllm / opd-stt, doc 10 §2)

```bash
docker stop opd-tts            # frees ~0.3 GB VRAM
docker start opd-tts
docker restart opd-tts         # after a config tweak

# If you ever `docker compose down` (removes opd_default), reconnect after up:
docker network connect opd_default opd-tts
```

## Later: the branded Dhara voice (reserved)

When you clone a warm **Dhara** voice in Voicebox, switch `TTS_PROVIDER=voicebox` +
`VOICEBOX_URL` + `VOICEBOX_VOICE` (doc 10 §6). This Kokoro container can stay running
as the fast English/fallback voice, or be stopped. No app code changes either way.
