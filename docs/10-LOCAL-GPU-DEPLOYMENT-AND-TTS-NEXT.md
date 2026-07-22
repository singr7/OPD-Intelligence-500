# 10 — Local GPU deployment (live) + next: local Dhara TTS

**Status as of 2026-07-22:** the pilot runs on an on-prem **RTX 4090 workstation**
(`omen`, Ubuntu, kernel 6.17, driver 595.71.05) with **STT + LLM fully local**.
Kiosk voice-in (Whisper) and department routing / summaries (Qwen3) run on the
GPU; **zero cloud AI**. TTS (read-aloud) is still the browser's local voice — the
next session makes it a natural, branded **Dhara** voice on-box.

Public URL: `https://opd.radpretation.ai` (kiosk `/kiosk`, board `/board`,
coordinator `/coordinator`), fronted by the box's existing **nginx** (TLS) →
web `:13000`, api `:18080` under `/api/` (prefix stripped). See §5.

---

## 1. What is running

| Piece | How | Notes |
|---|---|---|
| App stack | `docker compose` in `~/projects/opd` | postgres `:15432`, redis `:16379`, api `:18080`, web `:13000` (caddy **not** used — nginx fronts it) |
| LLM | container `opd-vllm` (vLLM/Qwen3-8B-AWQ) | on network `opd_default`, host `:18000`, `--gpu-memory-utilization 0.48`, tool-calling on |
| STT | container `opd-stt` (faster-whisper-server) | on `opd_default`, host `:18010`, model **`Systran/faster-whisper-large-v3`** int8 |
| TTS | **browser SpeechSynthesis** (kiosk `/kiosk/tts` code is merged; flip on with the §6 deploy step) | on-box Dhara voice via Voicebox |

Both model containers are attached to the compose network `opd_default` and the
api reaches them **by name**: `LOCAL_VLLM_BASE_URL=http://opd-vllm:8000/v1`,
`LOCAL_STT_URL=http://opd-stt:8000`. Model cache persists in `/opt/opd/hf`.

`.env` local-AI block (the switches that matter):
```
ENV=local
LLM_PROVIDER=local_vllm     LOCAL_VLLM_MODEL=qwen3-8b-awq
STT_PROVIDER=local_whisper  LOCAL_STT_MODEL=Systran/faster-whisper-large-v3
NEXT_PUBLIC_KIOSK_SERVER_STT=1
NEXT_PUBLIC_API_BASE=https://opd.radpretation.ai/api
# high host ports so nginx (80/443) and 8000/3000/5432 are untouched
API_HOST_PORT=18080  WEB_HOST_PORT=13000  POSTGRES_HOST_PORT=15432  REDIS_HOST_PORT=16379
```
`ENV=local` is deliberate: the production boot-gate requires *every* provider
(incl. realtime/messaging/telephony, all unbuilt) to be non-fake, so a true
`ENV=production` boot is impossible until those channels land. Fine for the pilot.

---

## 2. Ops — bring up / down cleanly

Run from `~/projects/opd`.

**Stop everything (keeps data, network, images — frees VRAM):**
```bash
docker compose stop            # stops app services (containers + network stay)
docker stop opd-vllm opd-stt   # stops the GPU models, freeing ~15 GB VRAM
```

**Start everything:**
```bash
docker start opd-vllm opd-stt  # GPU models first
docker compose start           # (or: docker compose up -d)
```

**Restart one model** (after an OOM or a config tweak):
```bash
docker restart opd-vllm        # or opd-stt
```

**⚠️ Do NOT `docker compose down` casually.** `down` removes the `opd_default`
network, which **disconnects `opd-vllm`/`opd-stt`** (they're attached to it). If
you must `down` (e.g. to recreate app containers), reconnect the models after
`up`:
```bash
docker compose up -d
docker network connect opd_default opd-vllm
docker network connect opd_default opd-stt
```

**After a reboot:** everything auto-starts — app services and both model
containers use `--restart unless-stopped`, and Postgres data is a named volume
(`opd_pgdata`). Just verify with the health checks in §3.

**Apply new code** (a `git pull` that changed backend/web):
```bash
git pull --ff-only
docker compose up -d --build api web    # rebuild web only when a NEXT_PUBLIC_* or web file changed
```

---

## 3. What to watch (health + resources)

```bash
# GPU — the #1 thing. Keep total well under 24 GB; no OTHER GPU job should creep in.
watch -n2 nvidia-smi
#   vLLM ~11.5 GB + Whisper ~3 GB + desktop/Voicebox ~2.5 GB ≈ 17 GB. Watch memory.free
#   never approaches 0, and temp stays < ~83°C.

# Containers healthy (app) + Up not Restarting (models)
docker compose ps
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'opd-vllm|opd-stt'

# Provider health (circuit-breaker state per provider)
curl -s localhost:18080/providers/health | python3 -m json.tool

# API alive
curl -s localhost:18080/health

# Errors
docker compose logs -f --tail=50 api     # 500s, provider fallbacks
docker logs --tail=50 opd-vllm           # CUDA OOM, tool-parse issues
docker logs --tail=50 opd-stt            # model load / download

# Disk — model cache + DB volume grow
df -h /                                   # box disk
du -sh /opt/opd/hf                        # HF model cache (~10 GB)
docker system df                          # image/volume usage

# Local AI actually being used (and priced) — should show local-vllm / local-whisper
docker compose exec postgres psql -U opd -d opd -c \
  "select provider,count(*) from usage_events group by provider order by 2 desc;"
```
**Red flags:** `opd-vllm` in `Restarting` (usually CUDA OOM → a stray GPU job
appeared, or lower `--gpu-memory-utilization`); `providers/health` showing a
local provider unhealthy (its container is down); `usage_events` showing `fake`
for llm/stt (the `.env` switches reverted — recreate api).

---

## 4. Gotchas already solved (don't re-debug these)

1. **Docker build DNS.** Ubuntu's `systemd-resolved` stub (`127.0.0.53`) isn't
   reachable from build containers → `apt`/`npm` fail to resolve. Fixed by adding
   `"dns": ["<upstream>", "8.8.8.8"]` to `/etc/docker/daemon.json` (keeping the
   nvidia runtime block) + `systemctl restart docker`.
2. **`python-multipart`** was missing from `backend/requirements.txt` (the image
   builds from it, not pyproject) → api crash-looped on the `/kiosk/stt` form
   route. Added (commit `1e4f0ce`).
3. **`seeds/` not in the image.** It lives at repo root, outside the `./backend`
   build context, but the code reads it at `/seeds` for seeding *and at runtime*
   (tree bank → kiosk bundle/routing). Fixed with a read-only bind-mount in
   compose (commit `a94ee7d`).
4. **Whisper model id.** `Systran/faster-whisper-large-v3-turbo` **does not exist**
   — use `Systran/faster-whisper-large-v3` (or `deepdml/faster-whisper-large-v3-turbo-ct2`
   for turbo). The image also ships `HF_HUB_OFFLINE=1`; pre-download into
   `/opt/opd/hf` or pass `-e HF_HUB_OFFLINE=0`.
5. **Shared GPU.** The box is a workstation (Slicer, Jupyter, Voicebox, desktop).
   The pilot needs ~15 GB; keep other GPU jobs off during the pilot.
6. **`docker compose down` removes `opd_default`** and disconnects the models (§2).

---

## 5. nginx (live, for reference)

A dedicated server block for `opd.radpretation.ai` (does not touch other apps —
nginx routes by `server_name`): `/` → web `:13000`, `/api/` → api `:18080` with
the prefix stripped (`proxy_pass http://127.0.0.1:18080/;`), `client_max_body_size
12m` (STT uploads), and WebSocket upgrade headers on `/api/` (queue board live-
sync at `/api/queue/ws`). `NEXT_PUBLIC_API_BASE=https://opd.radpretation.ai/api`.

---

## 6. NEXT SESSION — local, natural "Dhara" TTS on the kiosk

**Goal:** the kiosk read-aloud (questions + summary read-back) uses a **local,
natural, branded Dhara voice** on the GPU box instead of the browser's
SpeechSynthesis — completing "fully local voice" (STT ✅ + LLM ✅ + TTS).

**The backend provider layer already exists (S-OSS.0)** — this is the good news:
- `app/providers/tts.py` — `TTSProvider.synthesize(text, lang, voice?, sample_rate?) -> Speech(audio: AudioClip)`.
- `app/providers/local_oss/tts.py` — `LocalTTSProvider` (`POST {LOCAL_TTS_URL}/tts → {"audio": "<base64 wav>"}`) and `VoiceboxTTSProvider` (Voicebox REST). Config-selectable: `TTS_PROVIDER=local_tts|voicebox`, `LOCAL_TTS_URL`, `LOCAL_TTS_VOICE`, `VOICEBOX_URL`, `VOICEBOX_VOICE` (default `dhara_hi_v1`).
- `app/providers/registry.py` — `tts_chain()` / `get_tts_provider()`.

**The kiosk wiring is DONE (mirrors `/kiosk/stt`):**
1. ✅ **Backend: `POST /kiosk/tts`** in `app/routes/kiosk.py` — body `{text, lang}` →
   `with_fallback(tts_chain(settings), lambda p: p.synthesize(...))` inside
   `usage_scope(channel=KIOSK)` → returns `{audio (base64), mime, sample_rate,
   provider, voice}`. Unauthenticated, 24 kHz for natural browser playback.
   Fake-provider tests in `tests/test_kiosk.py`.
2. ✅ **Frontend:** `web/app/(kiosk)/kiosk/_lib/speech.ts` — `speak()` takes a server
   path when `NEXT_PUBLIC_KIOSK_SERVER_TTS=1` (`speakServer()` POSTs `/kiosk/tts`
   and plays the clip via an `Audio` element); the browser voice (`speakBrowser()`)
   is the fallback on flag-off / offline / any fetch-or-decode error. Build arg
   `NEXT_PUBLIC_KIOSK_SERVER_TTS` wired into `web/Dockerfile` + `docker-compose.yml`
   + `.env.example`.
3. **The engine on the box — REMAINING deploy step.** Two ways to get a natural
   voice; **decision made: go with Voicebox + a single cloned Dhara voice (a)** so
   it works now, one identity for en+hi. Bake-off (below) is the quality follow-up.
   - **(a) Voicebox (recommended — it's already installed on `omen`):** clone a
     warm "Dhara" voice from a short human sample (doc 08 §1), expose Voicebox's
     REST API, set `TTS_PROVIDER=voicebox` + `VOICEBOX_URL=http://<voicebox>:PORT`.
     This is also the S-OSS.3 voice-identity path — one voice across every channel.
   - **(b) A `/tts` container (Kokoro or a bake-off winner):** serve `POST /tts →
     {"audio": base64 wav}`, set `TTS_PROVIDER=local_tts` + `LOCAL_TTS_URL`. Kokoro
     is fast + natural for English; Hindi/Marathi/Telugu quality is the open
     question the **S-OSS.1 bake-off** answers (Qwen3-TTS / Chatterbox / IndicF5 /
     Parler — doc 08 §6). Until the bake-off, per-language routing can keep weak
     languages on the browser voice.

**Deploy checklist — WORKS-NOW path is a Kokoro `/tts` container (all code merged).**
Decision (operator): stand up a default on-box voice first with **Kokoro-82M**
(`TTS_PROVIDER=local_tts`, no cloning); the branded **Dhara** clone via Voicebox is
a **reserved later iteration**. Kokoro runs as a standalone container on
`opd_default`, a peer of `opd-vllm`/`opd-stt` — full step-by-step in
**[deploy/tts-kokoro/README.md](../deploy/tts-kokoro/README.md)**. In short:
1. `docker build -t opd-tts:latest deploy/tts-kokoro`
2. `docker run -d --name opd-tts --gpus all --network opd_default --restart
   unless-stopped -p 18020:8000 -v /opt/opd/hf:/root/.cache/huggingface opd-tts:latest`
3. Smoke-test: `curl localhost:18020/tts …` writes a playable wav (README step 3).
4. `.env`: `TTS_PROVIDER=local_tts`, `LOCAL_TTS_URL=http://opd-tts:8000`,
   `LOCAL_TTS_VOICE=` (blank = per-lang default), `NEXT_PUBLIC_KIOSK_SERVER_TTS=1`.
5. `docker compose up -d --build api web` (web rebuild required — build-time flag).
   Reconnect `opd-tts` to `opd_default` if you ever `down` (§2).
6. **VRAM:** vLLM (11.5) + Whisper (3) + Kokoro (~0.3) ≈ 15 GB — comfortable on the
   4090. (Voicebox later would be ~4.5 GB, ≈19 GB total — see §4 gotcha 5.)

**Reserved later iteration — the branded Dhara clone (Voicebox):** clone a warm
human voice once (doc 08 §1), expose Voicebox REST, switch `TTS_PROVIDER=voicebox`
+ `VOICEBOX_URL` + `VOICEBOX_VOICE`. ⚠️ The adapter's `POST /api/tts →
{"audio": …}` shape has **never been validated against a live Voicebox**
(STATE.md) — confirm it against the instance's `/openapi.json` first and adjust
`local_oss/tts.py` if it differs. **Quality follow-up (S-OSS.1 bake-off):** the
measured RTF/MOS bake-off (Qwen3-TTS / Chatterbox / IndicF5 / Parler) picks the
best hi/mr/te engine; it swaps in behind the same `local_tts` config, no code
change. Until then Kokoro is en+hi and mr/te 400 back to the browser voice.

**Verification (mirror STT):** `curl POST /kiosk/tts` returns audio;
`usage_events` shows `provider=voicebox`/`local-tts`; the kiosk reads a question
in the Dhara voice; browser fallback still works with the flag off.

**Watch the VRAM budget:** TTS adds ~4–5 GB (doc 08 §4). vLLM (11.5) + Whisper
(3) + TTS (4.5) ≈ 19 GB — fits on the 4090 only with other GPU jobs kept off.
```
