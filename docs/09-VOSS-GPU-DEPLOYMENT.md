# 09 — V-OSS Deployment Runbook (own-box / single-server)

**Audience:** the DevOps person standing up the platform. Follow top to bottom.
Every step has a ✅ **checkpoint** — do not proceed past a red one. **Companion:**
doc 08 (why V-OSS exists + the model choices). You do not need to read the app code.

**Chosen topology (approved):** **everything on one office server** — the app
(api, worker, Postgres, Redis, web), the voice gateway, and the V-OSS voice models
(LLM + STT + TTS) all run on your RTX 4090 box. The hospital reaches it over the
internet like any hosted product. A future **hybrid** (app in AWS + voice at the
office) is described in **§15, marked FOR APPROVAL — do not build it this iteration.**

> **Status of this document.** This is the *plan + runbook*. The app stack itself
> already exists (`make dev` brings it up — S1). The V-OSS **GPU pieces** it points
> at — the repo `docker-compose.gpu.yml`, the custom TTS `/tts` service, the
> Pipecat realtime service, `make gpu-up` — are committed in the **next build
> session (S-OSS.1/.2)**. Where a step needs one of those, it says so and gives you
> an equivalent `docker run` you can execute **now** with upstream images, so the
> box is proven before that code lands. Command blocks are operational instructions,
> not application code.

---

## 0. Fill these in before you start

| Placeholder | Meaning | Who provides | Value |
|---|---|---|---|
| `<PUBLIC_HOST>` | Public DNS name of the box (for TLS), e.g. `opd.example.com` | You | |
| `<PUBLIC_IP>` | Static public IP / DDNS of the office link | You | |
| `<JWT_SECRET>` | App auth secret, ≥32 random chars | `openssl rand -hex 32` | |
| `<DB_PASSWORD>` | Postgres password (not the dev default) | `openssl rand -hex 24` | |
| `<BACKUP_BUCKET>` | Off-box storage for nightly DB backups (S3 or any object store) | You | |
| `<HF_TOKEN>` | Hugging Face token for gated model pulls (if any) | You | |

**Box facts (already true):** RTX 4090 24 GB · Ubuntu 22.04+ · NVIDIA driver 550+ ·
Docker + `nvidia-container-toolkit` **already installed** (§3 only verifies them).
**Recommended host:** ≥12 CPU cores, ≥64 GB system RAM, ≥500 GB NVMe (the GPU is
only for the voice models; Postgres/app/Redis are CPU+RAM).

---

## 1. What you are deploying (one box, two stacks)

| Stack | Services | Exists **now**? | Runs on |
|---|---|---|---|
| **App** (S1–S6) | api, worker/beat, Postgres, Redis, web (kiosk/doctor/…), Caddy | ✅ Yes (`make dev`) | CPU + RAM |
| **Voice models** | vLLM (LLM), faster-whisper (STT) | ✅ Yes — upstream images (§6b/§6c) | **GPU** |
| | TTS `/tts` service (Dhara voice) | ⏳ Next session (custom wrapper) | GPU |
| | voice-gw + Pipecat realtime (VAD/barge-in) | ⏳ Next session (S-OSS.2) | CPU + a little GPU |
| **Bake-off** | Voicebox (TTS comparison + V3-pack batch) | ✅ Yes — for benchmarking | GPU |

**The big simplification of own-box:** the app talks to the voice models over
**localhost** (`127.0.0.1:8000` etc.), not over the internet. So the models are
**never exposed publicly** — no tunnel, no gateway token, no IP allowlist for them.
The only public surface is the app's own web/api (§7). This is simpler *and* safer
than any split topology.

**Definition of done:** app stack up, vLLM + STT GPU-healthy on localhost, the app
configured to use them (`LLM_PROVIDER=local_vllm`, `STT_PROVIDER=local_whisper`),
the public web/api reachable over authenticated HTTPS, nightly DB backup running,
and the §12 latency self-test green from the hospital's network. (TTS-live and
realtime land next session; until then TTS stays on cloud/`fake`.)

---

## 2. Why voice works here (and the one thing to watch)

**The rule that governs voice quality:** the real-time loop —
`voice-gw → VAD → STT → LLM → TTS → back` — must be **co-located** so its hops are
localhost. On this single box, they are. That is what removes the VAD/barge-in and
turn-latency problems: there is **no WAN inside the loop** (doc 08 §2, ≤3.0s p90).

Two facts worth internalizing:
- **The GPU does not need to be near the patient.** The patient's audio arrives as
  **one managed media stream** (Exotel WS for phone, browser WS/WebRTC for kiosk) —
  exactly as it would to any cloud voice service. Normal jitter on that single
  inbound stream is absorbed by jitter buffers; it is the *internal* STT↔LLM↔TTS
  hops that must not cross a WAN, and here they don't.
- **You gave up nothing by not putting the GPU in the hospital.** That placement
  only ever bought LAN kiosks surviving a total internet outage — and the tier
  ladder already falls to **V3 (zero-AI, pre-recorded)** during an outage, so no
  patient is turned away regardless.

**The one thing to watch: your office internet uplink.** Because the whole product
lives at the office, the hospital reaches it over that link, and phone media
(Exotel ⇄ box) rides it too. This is the single production dependency you now own —
harden it in **§9**.

### 2.1 Two-box variant — separate GPU node on the same LAN ✅ (approved)

If the current machine's GPU is already committed to another app, run the **voice
models on a second GPU machine on the same LAN** — this is fully supported and, if
anything, *cleaner* than one box: V-OSS gets the whole 24 GB instead of sharing.

```
  Box A (app)                         Box B (GPU node)
  api · worker · Postgres · Redis     vLLM (LLM)
  web · Caddy · voice-gw       ⇄ LAN ⇄ faster-whisper (STT)
  (public-facing)              <1ms   TTS /tts  (next session)
```

**Why it keeps voice quality:** the governing rule (§2) is "no WAN inside the
real-time loop." A same-switch **LAN hop is sub-millisecond with ~zero jitter** —
indistinguishable from localhost for VAD/barge-in/turn latency. Audio across the
LAN is tiny vs 1 GbE. This is the standard "dedicated inference node" pattern, **not**
the hybrid (§15) — there is still no WAN in the loop, so no approval gate applies.

**The only three changes from the single-box runbook:**
1. **Model URLs point at Box B's LAN IP**, not localhost. In Box A's `.env`:
   ```
   LOCAL_VLLM_BASE_URL=http://<GPU_BOX_LAN_IP>:8000/v1     # e.g. http://192.168.1.20:8000/v1
   LOCAL_STT_URL=http://<GPU_BOX_LAN_IP>:8010
   ```
   And on Box B, bind the model containers to the LAN interface (`-p 8000:8000`
   instead of `-p 127.0.0.1:8000:8000`) so Box A can reach them.
2. **Firewall Box B to Box A only.** Box B has **no public IP and no inbound from
   the internet**; allow `8000/8010` **only from Box A's LAN IP**:
   ```bash
   sudo ufw default deny incoming && sudo ufw allow 22
   sudo ufw allow from <BOX_A_LAN_IP> to any port 8000 proto tcp
   sudo ufw allow from <BOX_A_LAN_IP> to any port 8010 proto tcp
   sudo ufw enable
   ```
   The models are LAN-private; the public surface stays only on Box A (§7). (The
   STT/TTS adapters send no auth header yet — on a trusted, firewalled private LAN
   that is fine; the IP restriction is the control. Same caveat as §8, LAN-scoped.)
3. **`voice-gw` placement:** keep it on Box A (talks to Box B over the LAN — fine),
   *or* co-locate it on Box B next to the models (marginally tighter, not required).
   Either meets the latency budget; the next-session compose supports both.

Everything else in this runbook is unchanged — §5/§6b/§6c/§10 run on **Box B**,
the app stack + §7 public exposure + §9 uplink + §14 backups run on **Box A**. Add
`<GPU_BOX_LAN_IP>` / `<BOX_A_LAN_IP>` to your §0 record. Both boxes want a UPS (§9).

---

## 3. Verify prerequisites (assumed installed)

```bash
nvidia-smi                       # driver 550+, one RTX 4090, 24 GB
docker --version                 # 24+
docker compose version           # v2
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

✅ **Checkpoint:** the last command prints the GPU from *inside* a container. If it
errors "could not select device driver ... gpu", wire the toolkit into Docker:
`sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.

Working layout:
```bash
sudo mkdir -p /opt/opd/{models,backups} && sudo chown -R "$USER" /opt/opd
git clone <your-repo-url> /opt/opd/app && cd /opt/opd/app   # the platform repo
```

---

## 4. VRAM budget (RTX 4090, 24 GB)

Only the **voice models** use the GPU; Postgres/app/Redis do not touch it.
Everything below must coexist on the one card — the numbers leave headroom on
purpose (doc 08 §2).

| Component | VRAM | Notes |
|---|---|---|
| vLLM — Qwen3-8B AWQ weights + KV cache | ~11 GB | `--gpu-memory-utilization 0.48` caps it so STT/TTS fit |
| faster-whisper large-v3-turbo (int8) | ~3.5 GB | 3 workers, batched |
| TTS (winner) + Kokoro fallback | ~4–5 GB | next-session service |
| VAD + endpointing (Silero + smart-turn) | ~0.5 GB | next-session (Pipecat) |
| **Headroom** | **~4–5 GB** | burst KV, model swaps, safety |

> ⚠️ Most common failure: vLLM grabs the whole card and starves STT/TTS with a CUDA
> OOM. `--gpu-memory-utilization 0.48` (§6b) prevents it. Do not remove it.

---

## 5. Pre-pull the models

Into `/opt/opd/models` so restarts are cold-start-fast (doc 08 §4: <3 min).

```bash
export HF_TOKEN=<HF_TOKEN>            # only if a model is gated
pip install --user "huggingface_hub[cli]"

huggingface-cli download Qwen/Qwen3-8B-Instruct-AWQ \
  --local-dir /opt/opd/models/qwen3-8b-awq
huggingface-cli download Systran/faster-whisper-large-v3-turbo \
  --local-dir /opt/opd/models/whisper-large-v3-turbo
```

> **Verify current repo names before pulling** — model names drift; if one 404s,
> use the current canonical repo and record what you pulled in the §0 table.

✅ **Checkpoint:** `du -sh /opt/opd/models/*` shows two non-empty dirs.

---

## 6. Bring up the stacks

### 6a. App stack (exists today)

Create `/opt/opd/app/.env` from `.env.example`, with production values:
```
ENV=production
JWT_SECRET=<JWT_SECRET>
DATABASE_URL=postgresql+asyncpg://opd:<DB_PASSWORD>@postgres:5432/opd
OTP_DEBUG_ECHO=false
# V-OSS: the app reaches the models on localhost (see §6b/§6c)
LLM_PROVIDER=local_vllm
LOCAL_VLLM_BASE_URL=http://host.docker.internal:8000/v1
STT_PROVIDER=local_whisper
LOCAL_STT_URL=http://host.docker.internal:8010
TTS_PROVIDER=sarvam        # cloud TTS until the local /tts ships (next session)
REALTIME_PROVIDER=fake     # local-pipecat lands next session (S-OSS.2)
# ... plus SARVAM_API_KEY etc. for the cloud fallbacks you keep
```
Then bring the app up and migrate:
```bash
make dev            # or: docker compose up -d   (postgres, redis, api, worker, web, caddy)
make migrate && make seed
```
✅ **Checkpoint:** `docker compose ps` all healthy; `curl -s localhost:8080/health`
(api) returns ok. `assert_production_safe` will refuse to boot if any provider is
still `fake` where it shouldn't be, or the JWT secret is the dev default — that is
expected and good.

> `host.docker.internal` lets the api container reach the model containers running
> on the host. On Linux, add `--add-host=host.docker.internal:host-gateway` to the
> api service (the next-session compose will bake this in).

### 6b. vLLM — the LLM (deployable now)

```bash
docker run -d --name opd-vllm --restart unless-stopped --gpus all \
  -v /opt/opd/models:/models \
  -p 127.0.0.1:8000:8000 \
  vllm/vllm-openai:latest \
  --model /models/qwen3-8b-awq --served-model-name qwen3-8b-awq \
  --quantization awq --max-model-len 8192 --gpu-memory-utilization 0.48 \
  --enable-auto-tool-choice --tool-call-parser hermes
```
- `--served-model-name qwen3-8b-awq` must match `LOCAL_VLLM_MODEL` (default) and
  the `local-vllm` price-book row.
- `--enable-auto-tool-choice`/`--tool-call-parser hermes` turn on **function
  calling** — the intake tool contract needs it (doc 08 §5). If tool calls come
  back empty in §11, this flag is the first thing to revisit.
- Bound to `127.0.0.1` — never publicly exposed; the app reaches it on localhost.

✅ `curl -s localhost:8000/v1/models | grep qwen3-8b-awq`

### 6c. faster-whisper — the STT (deployable now)

```bash
docker run -d --name opd-stt --restart unless-stopped --gpus all \
  -v /opt/opd/models:/models \
  -e WHISPER__MODEL=/models/whisper-large-v3-turbo -e WHISPER__COMPUTE_TYPE=int8 \
  -p 127.0.0.1:8010:8000 \
  fedirz/faster-whisper-server:latest-cuda
```
- Exposes the OpenAI audio API (`POST /v1/audio/transcriptions`) — exactly what
  `LocalSTTProvider` calls. Image/env names vary by version (also published as
  **Speaches**); verify from its README, the contract you need is only
  `/v1/audio/transcriptions → {"text": ...}`.

✅ `ffmpeg -f lavfi -i "sine=frequency=200:duration=1" -ac 1 -ar 16000 /tmp/t.wav -y`
then `curl -s -F file=@/tmp/t.wav -F model=whisper-large-v3-turbo localhost:8010/v1/audio/transcriptions`
→ JSON with a `text` field.

### 6d. TTS + realtime — next session

The live `/tts` service (Dhara voice) and the Pipecat realtime pipeline are
committed next session (the TTS engine is only chosen after the §10 bake-off).
Until then keep `TTS_PROVIDER=sarvam` (cloud) and `REALTIME_PROVIDER=fake`. The
kiosk (V3/tap) and V2 pipeline already work with local LLM+STT.

---

## 7. Public exposure & TLS (the app surface only)

Only the **app's web/api** faces the internet (the hospital's kiosks, doctors,
and the Exotel/WhatsApp webhooks hit it). The **models stay on localhost** and are
never proxied. The repo already ships Caddy in the compose stack; point it at your
host and it will fetch a Let's Encrypt cert automatically.

Set in the app stack (Caddy config / env):
```
PUBLIC_HOST=<PUBLIC_HOST>     # Caddy gets the cert for this; HTTP->HTTPS auto
```
Firewall — expose **only** 443:
```bash
sudo ufw default deny incoming && sudo ufw allow 22 && sudo ufw allow 443 && sudo ufw enable
```
✅ **Checkpoint:** from an outside network, `https://<PUBLIC_HOST>/health` returns
ok over a valid cert; `8000/8010/5432/6379` are **not** reachable from outside
(`nmap <PUBLIC_IP>` shows only 443/22).

---

## 8. (Removed — models are localhost, no gateway needed)

Own-box has no model gateway, no bearer token for the models, no IP allowlist.
That machinery only returns in the **hybrid** (§15), where EC2 reaches the models
over the internet. Noted here so no one re-adds it by habit.

---

## 9. Uplink & power resilience (your one production dependency)

Because the whole product is at the office, the office link *is* production.
- **Primary:** business fibre with a static IP (or reliable DDNS).
- **Failover:** a 4G/5G router in automatic failover, so a fibre cut doesn't drop
  live calls or the hospital's kiosks.
- **UPS** on the box + network gear (a 4090 workstation pulls real power).
- **What happens if the office goes fully dark:** the hospital's channels fall to
  **V3** (and cloud **V2** if you keep cloud keys) via the tier ladder — degraded,
  never denied (doc 08 §7). Confirm this is acceptable to the hospital and document
  the drill.

---

## 10. Health, metrics, and the bake-off

**GPU metrics** (so they exist when Grafana is added):
```bash
docker run -d --name opd-dcgm --restart unless-stopped --gpus all \
  --cap-add SYS_ADMIN -p 127.0.0.1:9400:9400 nvcr.io/nvidia/k8s/dcgm-exporter:latest
```
✅ `curl -s localhost:9400/metrics | grep DCGM_FI_DEV_GPU_UTIL` returns a value.

**Bake-off (S-OSS.1 AC).** Install Voicebox on the box (doc 08 §1); run the TTS
engine comparison + Whisper WER bench; results to `benchmarks/oss-voice/`. This
decides the TTS engine, so it precedes shipping the live `/tts`. Full harness spec:
doc 08 §6.

---

## 11. End-to-end verification

```bash
# LLM chat + tool-call sanity (localhost)
curl -s localhost:8000/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"qwen3-8b-awq","messages":[{"role":"user","content":"Say namaste in one word"}]}'
# STT (localhost)
curl -s -F file=@/tmp/t.wav -F model=whisper-large-v3-turbo localhost:8010/v1/audio/transcriptions
```
Then drive a **real kiosk intake** through the public URL and confirm it completes.
Optionally point the app's provider contract tests at the live box.

✅ **Checkpoint:** an intake completes end-to-end using `local_vllm` + `local_whisper`;
`usage_events` rows show `provider=local-vllm` / `local-whisper` with non-zero cost.

---

## 12. Latency self-test (from the hospital's network)

Run from a machine **on the hospital's internet**, hitting the public URL — this is
the path a patient's browser/phone actually takes.

```bash
ping -c 20 <PUBLIC_HOST>                                    # avg + max (jitter)
for i in $(seq 10); do                                      # LLM turn latency, localhost-fast
  curl -s -o /dev/null -w "%{time_total}\n" localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen3-8b-awq","max_tokens":40,"messages":[{"role":"user","content":"One short sentence."}]}'
done | sort -n | tail -2
```
**Interpretation:** because the STT↔LLM↔TTS loop is localhost, per-turn compute
should sit comfortably under the ≤3.0s budget. The only WAN factor left is the
single patient↔box media stream; watch the `ping` **jitter/loss** to the hospital,
not raw RTT. High loss/jitter on that link is an uplink problem (§9), not a model
problem. Record the numbers in §0.

---

## 13. Security checklist (sign off before go-live)

- [ ] Models (`8000`/`8010`/`9400`) bound to `127.0.0.1` only; never public.
- [ ] Firewall: only `443` (+ `22` from your admin IP) open; DB/Redis not reachable outside.
- [ ] Caddy TLS valid for `<PUBLIC_HOST>`; HTTP→HTTPS redirect on.
- [ ] `JWT_SECRET` ≥32 random chars; `OTP_DEBUG_ECHO=false`; `<DB_PASSWORD>` not the dev default.
- [ ] `assert_production_safe` passes (no `fake` provider in a production slot).
- [ ] Nightly DB backup runs and a **restore has been tested once** (§14).
- [ ] Every container `--restart unless-stopped`; UPS + uplink failover in place (§9).

---

## 14. Ops

| Task | Command |
|---|---|
| See everything | `docker ps` · `nvidia-smi` (VRAM per process) |
| Restart a model | `docker restart opd-vllm` (or `opd-stt`) |
| Logs | `docker logs -f --tail=100 opd-vllm` |
| Update a model/version | pull new weights to `/opt/opd/models`, bump image tag, `docker rm -f` + re-run that one service |
| **Nightly DB backup** | `pg_dump` → gzip → upload to `<BACKUP_BUCKET>` (cron; keep 14 days). **Restore drill once before go-live.** |
| CUDA OOM at startup | lower `--gpu-memory-utilization`; check no orphaned container holds VRAM (`nvidia-smi`, `docker ps -a`) |
| A call fails | `docker ps` up? → curl the §11 localhost checks → api logs → uplink (§9) |

**Failure domain:** one box, one GPU = one point of failure — intentional for a
pilot. The app's tier ladder (V-OSS → cloud V2 → V3) is the HA story (doc 08 §7).
Do not buy a second GPU or make the box HA for the pilot; do make the **backup +
uplink failover** solid, because those protect data and continuity, not compute.

---

## 15. Hybrid deployment — ⛔ FOR APPROVAL, NEXT ITERATION (do not build now)

> **Status: proposed, not approved. Do not implement this iteration.** Requires
> sign-off from the product owner before any work starts.

**What it is:** move the **app tier (api, DB, dashboards) to AWS EC2** for cloud
durability/backups/scaling, while keeping the **voice-gw + Pipecat + models at the
office** so the real-time loop stays co-located (§2's rule is preserved). EC2
becomes the control + data plane; the office edge does the live audio.

```
Patient ⇄ Exotel/kiosk ⇄ [office: voice-gw + Pipecat + STT/LLM/TTS]
                                │  async over WAN (off the live path)
                                └──────────▶ [AWS EC2: api / Postgres / dashboards]
```

**Why you might want it later:** managed database backups, cloud monitoring, and
scaling the non-voice app independently of the office box — without giving up
local-loop voice or ₹0-per-call voice.

**What it costs / what must be built first (the approval checklist):**
1. **Split the deployment:** voice-gw becomes an *edge* service co-located with the
   box; EC2 runs everything else. Changes the S19 deployment plan.
2. **Secure office↔EC2 link returns:** the model gateway, bearer token, and IP
   allowlist that own-box removed (§8) must be built — plus WireGuard or mutual TLS.
3. **Add auth headers to `LocalSTTProvider`/`LocalTTSProvider`** (they don't send
   one today — fine on localhost, unsafe over a WAN).
4. **Async intake/usage sync** from the office edge to the EC2 API, with a
   store-and-forward queue for link blips.
5. **Measured latency** from EC2 region → office confirming the async path is truly
   off the live loop.

**Recommendation:** stay on own-box (§1–§14) for the pilot. Revisit the hybrid only
when cloud durability or independent app scaling becomes a real need — and only
after explicit approval, as a planned iteration with the checklist above scoped as
its own session.

---

## 16. Handoff to the build team (what the next session wires)

1. Commit `docker-compose.gpu.yml` + `make gpu-up`/`make gpu-bench`; pin the exact
   images/tags this runbook verified; add the api `host.docker.internal` host-gateway.
2. Ship the TTS `/tts` service (bake-off winner + Kokoro fallback); then set
   `TTS_PROVIDER=local_tts`.
3. Ship `LocalPipelineVoiceProvider` (Pipecat: Silero VAD + smart-turn + barge-in),
   wire `config/tiers.yaml` `ladder_for()` + `AdmissionController` into voice-gw.
4. (Hybrid only, if/when approved) model gateway + STT/TTS adapter auth headers +
   async EC2 sync (§15).

---

## 17. Still needed from you

- **Public hostname + static IP/DDNS** (`<PUBLIC_HOST>`/`<PUBLIC_IP>`) — required for TLS.
- **Off-box backup target** (`<BACKUP_BUCKET>`) — where nightly DB dumps land.
- **Uplink plan** — fibre + 4G/5G failover + UPS (§9); confirm the V3-fallback drill
  is acceptable to the hospital.
- **TTS engine** — output of the §10 bake-off; live TTS stays on cloud until then.
```
