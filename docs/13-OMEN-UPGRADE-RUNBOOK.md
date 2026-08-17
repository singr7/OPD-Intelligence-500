# 13 — Upgrading the omen box (and getting back off it)

**For:** taking the pilot box at `opd.radpretation.ai` from whatever it is running
to a newer commit, with a rollback you have actually tested before you need it.
Written for the S-GL.1 + S-GL.2 upgrade; the shape applies to any of them.

Run everything from `~/projects/opd` on the box. Ports are omen's (doc 10 §1):
api **18080**, web **13000**, postgres **15432**.

---

## 0. Standing facts about this box

Four things are true of omen regardless of which release you are installing.
They are here because each one used to be assumed the other way round, and each
assumption made an upgrade more delicate than it needed to be.

### 0.1 Omen carries no real patient data

**Operator decision, 2026-08-17.** Everything in omen's database is seed and test
data. It is therefore acceptable — and often the fastest path — to **rebuild the
database and re-seed it** rather than preserve it across an upgrade.

What this changes:

- `make seed` is **allowed** on this box, not forbidden. Doc 20 §3's "do not run
  `make seed` or `make slots` on Omen" was written when the box was treated as
  holding pilot data; it no longer applies. Since AYUR-1 the loader's
  `_console_owned` rule never overwrites a row an administrator can edit, so a
  re-seed adds what is missing and leaves console edits alone.
- A **failed migration is not a crisis.** Restore the checkpoint dump, or drop
  and recreate the schema, whichever is quicker.
- `omen-rollback.sh --with-db` carries **no data-loss argument against it here.**
  §8's warning about discarding real intakes is about a box with patients on it.
  It stays in this document because it will be true again the day omen takes a
  real patient — but today, use it freely.

**The day this stops being true, delete this subsection.** A box that quietly
acquires real data while a document says it has none is worse than one that never
said anything.

### 0.2 Omen is a hybrid: local models, with a cloud model for vision only

**Operator decision, 2026-08-17.** Omen no longer runs pure-local AI. The intake
routing and summaries stay on the GPU; **the MRD extraction call goes to a cloud
vision model.**

This needs **no code change and no per-feature provider setting** — it is the
existing fallback chain doing what it was built for:

```
LLM_PROVIDER=local_vllm          # unchanged: routing, summaries, research stay on the 4090
LLM_FALLBACK_PROVIDER=gemini     # or openai
GEMINI_API_KEY=<key>
```

The mechanism, worth understanding before you change it: `LLMProvider.
supports_images` is `False` on the local vLLM, and `complete()` **refuses** a
request carrying images rather than stripping them and answering anyway
(`app/providers/llm.py`). The refusal is `UnsupportedCapability`, which subclasses
`ProviderUnavailable` — so `with_fallback` treats it as an outage of that one
provider and **walks to the next link in the chain**. MRD's extraction is the only
call in the system that attaches images, so it is the only call that falls
through. Every text call is answered by the GPU and never reaches the vendor.

Consequences to accept deliberately:

- **Scanned patient records leave the box.** That is the whole point of the
  decision, but it is a change in the privacy posture doc 10 opens by claiming
  ("zero cloud AI"). Doc 10 §1 is now wrong for omen; read this subsection as the
  correction.
- **The fallback is a real fallback, for text too.** If the GPU dies or vLLM
  crash-loops, text completions now silently succeed against the cloud instead of
  failing. Usually what you want; check `usage_events` rather than assuming the
  GPU is healthy because the kiosk works.
- **Gemini Flash is the documented vision provider** for doc 21 (see
  `registry.py`'s Gemini→OpenAI pairing). Prefer it over OpenAI unless there is a
  reason.
- `MRD_EXTRACT_TIMEOUT_SECONDS=60` matters here. At the 10s class default every
  extraction on the AWS box failed and five in a row opened the circuit breaker,
  while the vendor was healthy and answering in 1.3s.

### 0.3 There is no ledger of what omen runs — so make one

Doc 18 §0 records the cloud box's deployed SHA and is the reason a rollback there
has an argument to pass. Nothing records omen's. Until the table below has rows,
**the box is the only authority**, and step one of any upgrade is:

```bash
cd ~/projects/opd
git rev-parse HEAD                     # ← the previous SHA a rollback needs
docker compose exec -T postgres psql -U opd -d opd -tAc "select version_num from alembic_version"
```

**Update this table at the end of every omen deploy.**

| deployed (UTC) | release SHA | previous SHA | commits | alembic head after | notes |
|---|---|---|---|---|---|
| _(no deploy recorded — the rows above this line start here)_ | | | | | |

As of 2026-08-17 omen's alembic revision is believed to be **`a4d5e6f7b801`**,
inferred from the nine-pending-migration list in HANDOFF and confirmed by
`c6e3681f5ce1.down_revision`. Believed, not known: verify on the box.

### 0.4 Never `docker compose down`

It removes the `opd_default` network and disconnects `opd-vllm` / `opd-stt`
(doc 10 §2). `docker compose up -d` is the only thing you need.

---

## 0.5 The one-paragraph version

The kiosk is what must not break. Everything in S-GL.1 and S-GL.2 is **additive** —
two new database tables, two new admin tabs, no change to the kiosk, queue, board
or doctor path — so the risk is not the features, it is the *rebuild*: the web
container that serves the kiosk gets replaced. So: checkpoint, **build before you
switch**, migrate, verify, and keep a rollback that is a retag rather than a
rebuild.

---

## 1. What you are installing, and what it changes

| | |
|---|---|
| **S-GL.1** | the switchboard: per-channel on/off, vendor credentials in the console. Migration `2c978d44c900` — **two new tables** (`channel_configs`, `provider_secrets`), no change to any existing table. New Python dependency: `cryptography`. |
| **S-GL.2** | People & roster: onboard staff and doctors, author or import the weekly clinic grid. **No migration.** No new dependency. |

**What changes for a patient on day one: nothing.** With nothing published from
the console, channel resolution falls back to `config/tiers.yaml`, which ships
every channel `enabled: true` — the same behaviour as before the upgrade. The
kiosk is additionally *ready by construction* (it needs no vendor), so the new
channel gate cannot close it.

**What changes for you:** the admin console goes from six tabs to eight.

### The failure this upgrade nearly had

`cryptography` was added to `backend/pyproject.toml` but not to
`backend/requirements.txt`, which is what the **Docker image** installs. Tests run
in the venv (pyproject) and were all green; the image would have built fine and
then crash-looped on `import app.main`. Fixed, and `make preflight` now exists to
catch the whole class:

```bash
make preflight     # builds the api + voice-gw images and proves they can import
```

**Run it on your laptop before you touch the box.** It is the gap between "tests
are green" and "the container boots", and this repo has fallen into it twice
(`python-multipart`, then `cryptography`).

---

## 2. Before you start (laptop, 5 minutes)

```bash
git pull
make test          # 1212 backend / 25 voice-gw / 48 web / 6 android
make preflight     # the images actually boot
```

Both green → proceed. Either red → stop; nothing below fixes it.

---

## 3. On the box: take the checkpoint

This is the step that makes everything after it reversible. Do not skip it.

```bash
cd ~/projects/opd
git fetch origin                       # fetch only; nothing is applied yet
./deploy/omen-checkpoint.sh
```

It prints a stamp like `20260727-140312` and saves, under
`~/opd-checkpoints/<stamp>/`:

- the running **commit** (also as git tag `omen-checkpoint-<stamp>`),
- the running **images**, retagged `opd-rollback/<service>:<stamp>` — so a
  rollback is a retag, not a rebuild, because the build is often the thing that
  broke,
- a gzipped **pg_dump** of the database,
- the current **alembic revision** and a copy of **`.env`**.

**Write the stamp down.** Everything in §7 needs it.

---

## 4. Pre-flight checks on the box

```bash
# Disk — you are about to add a set of images.
df -h / && docker system df

# The build args that get baked into the kiosk bundle. NEXT_PUBLIC_API_BASE
# MUST read https://opd.radpretation.ai/api. If it says localhost:8000, stop —
# .env is not being read and a rebuild would produce a broken kiosk.
docker compose config | grep NEXT_PUBLIC

# What is running now, so you can compare afterwards.
docker compose ps
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'opd-vllm|opd-stt'
curl -s localhost:18080/health
```

---

## 5. Pull and build — **without switching anything over**

Building does not touch a single running container. If the build fails, you are
still on the old code, still serving patients, and there is nothing to undo.

```bash
git pull --ff-only origin main
docker compose build            # 5–15 min; the web build is the slow, RAM-hungry one
```

If the web build gets OOM-killed (it is a Next.js production build on a box also
running vLLM), free memory and retry, or build it alone:

```bash
docker stop opd-stt             # ~3 GB back; the kiosk mic is down while it is
docker compose build web
docker start opd-stt
```

**A failed build here costs you nothing.** Go to §7 only if you have already
switched over.

---

## 6. Switch over

Order matters: schema first (the new tables are additive, so the *old* code is
still perfectly happy once they exist), then containers.

```bash
# 6a. Migrate. Run it inside the api image so you do not need a venv on the box.
docker compose run --rm \
  -e ALEMBIC_DATABASE_URL=postgresql+asyncpg://opd:${POSTGRES_PASSWORD:-opd_local_dev}@postgres:5432/opd \
  api alembic upgrade head

# Confirm:
docker compose exec -T postgres psql -U opd -d opd -tAc "select version_num from alembic_version"
#   -> 2c978d44c900

# 6b. Start the new containers. `up -d`, never `down`.
docker compose up -d

# 6c. Watch them come up.
docker compose ps
docker compose logs --tail=40 api
```

Do **not** run `make seed` or `make slots`. There is no new seed data, and the box
already has its own.

---

## 7. Verify (in this order — cheapest and most important first)

### 7a. The stack is alive

```bash
curl -s localhost:18080/health                       # {"status":"ok"...}
curl -s localhost:13000/api/health                   # web
docker compose ps                                    # all healthy, none Restarting
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'opd-vllm|opd-stt'   # still Up
curl -s localhost:18080/providers/health | python3 -m json.tool | head -30
```

### 7b. The GPU containers are still attached

The rebuild recreated the app containers. If anything ran `down`, these models are
now off the network and every local LLM/STT call fails:

```bash
docker compose exec -T api python -c \
  "import httpx; print(httpx.get('http://opd-vllm:8000/health', timeout=5).status_code)"
docker compose exec -T api python -c \
  "import httpx; print(httpx.get('http://opd-stt:8000/health', timeout=5).status_code)"
```

Both `200`. If they fail:

```bash
docker network connect opd_default opd-vllm
docker network connect opd_default opd-stt
```

### 7c. **The kiosk still works** — the only test that really matters

In a browser: <https://opd.radpretation.ai/kiosk>

- pick a language, speak the chief complaint (proves Whisper on the GPU),
- answer the tree by tap, confirm the read-back,
- **take a token** — and check it appears on <https://opd.radpretation.ai/board>.

Then confirm the local models were actually used, not a fake:

```bash
docker compose exec -T postgres psql -U opd -d opd -c \
  "select provider, count(*) from usage_events
    where created_at > now() - interval '15 minutes' group by 1 order by 2 desc;"
```
Expect `local-vllm` / `local-whisper`. Seeing `fake` means the `.env` switches did
not survive — that is a rollback trigger.

### 7d. The two new tabs

<https://opd.radpretation.ai/admin> — sign in as the admin phone.

**Channels tab** (S-GL.1). Expect: four rows; **kiosk Open**; WhatsApp and phone
showing *why* they are not (no credentials); and the banner reading *"Running from
config/tiers.yaml — nothing has been published from this console yet."*

> On this box `ENV=local`, so a `fake` provider counts as configured **and says
> so**. A row reading *"running the fake provider — no real vendor is connected"*
> is correct. A row reading plain "configured" for WhatsApp would be the tab
> lying, and is worth reporting.

**Do not press Publish on this tab during a test run.** Publishing a channel
document is the one action here that can shut the OPD in one tap.

**People & roster tab** (S-GL.2). Expect the five seeded doctors, their clinics in
the week grid, and their real slot counts. Read-only inspection is enough for a
test run — but this is also the box where onboarding the hospital's *real* doctors
and importing their *real* roster is the natural next job (HANDOFF, "Owed on
omen").

If you do write something, note that it is real: a created doctor stays (deactivate
is not delete), and an imported clinic generates real bookable inventory. Use the
**Preview** button — an import previews by default and writes nothing until you
press Apply.

### 7e. Nothing regressed elsewhere

```bash
docker compose logs --since=10m api | grep -iE " 500 |traceback|error" | head -20
```

---

## 8. Rollback

**Trigger it if:** the api or web container will not stay healthy; the kiosk
cannot complete an intake; `usage_events` shows `fake` for llm/stt and `.env`
looks right; or anything you cannot explain in ten minutes. Roll back first,
diagnose afterwards — the box has patients in front of it.

```bash
cd ~/projects/opd
./deploy/omen-rollback.sh <stamp>
```

That restores the **code** (checks out the checkpointed commit) and the
**images** (retags the saved ones, no rebuild), then `docker compose up -d`.
Takes under a minute.

**It deliberately does not restore the database**, and by default you should not
ask it to — every migration this repo ships is additive, so the old code runs
against the migrated schema perfectly happily, and a restore is simply
unnecessary work.

> **The paragraph that used to be here no longer applies to omen.** It argued
> that restoring the dump would throw away real intakes, tokens and consult
> notes. Per §0.1 this box holds no real data, so `--with-db` costs nothing and
> is a legitimate first move if anything about the database looks wrong.
> **Restore this warning to full force the day omen sees a real patient.**

If the *data* is wrong — or you simply want a clean slate:

```bash
./deploy/omen-rollback.sh <stamp> --with-db     # asks you to type RESTORE
```

### Rolling the schema back too (rarely, and only if you insist)

Do it **before** checking out the old code — the old commit does not contain the
migration file, so it cannot downgrade it. And only if no vendor credentials have
been entered, since that table is where they live:

```bash
docker compose run --rm \
  -e ALEMBIC_DATABASE_URL=postgresql+asyncpg://opd:${POSTGRES_PASSWORD:-opd_local_dev}@postgres:5432/opd \
  api alembic downgrade -1
./deploy/omen-rollback.sh <stamp>
```

### Getting back to the tip after a rollback

```bash
git checkout main && git pull --ff-only
docker compose build && docker compose up -d
```

---

## 9. Afterwards

- **Set `SECRETS_KEY` in `.env`** before entering any real Meta or Exotel
  credentials. Left empty it is derived from `JWT_SECRET`, which works but couples
  the two: rotating the JWT secret makes every stored credential unreadable.
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
  Then `docker compose up -d api worker beat voice-gw` to pick it up.
- **Keep the checkpoint for a few days.** `~/opd-checkpoints/<stamp>/` is a few
  hundred KB plus the saved images; delete old ones with
  `docker image rm $(docker images 'opd-rollback/*' -q)` once you are confident.
- **Record what you observed** — the S-GL.3 acceptance criterion is a written
  record of each on-box item, and half of them are the things you just did.
