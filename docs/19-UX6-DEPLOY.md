# 19 — Deploying the S-UX.6 rejig

Everything in `SESSION-UX6` is code and seed content. There is **no migration**:
`Patient.age`, `Patient.sex` and `Patient.phone` already existed — the kiosk had
simply never filled them. So a deploy is: pull, reseed the two revised trees,
rebuild, restart, smoke.

Two things need a rebuild rather than a restart:

- **`web`** — `NEXT_PUBLIC_*` values and the whole kiosk bundle are baked at
  build time. A restart serves the old screens.
- **`api`** — the image copies `backend/`; it is not bind-mounted in production.

---

## Local / Omen

```bash
git pull --ff-only                 # on main
make preflight                     # proves both images still import
make dev                           # build + up + wait for health
make migrate                       # no new revisions, but keeps the head honest
make seed                          # publishes gynae_routing v2, general_medicine_routing v2
```

Then the smoke, in this order — each one checks a different thing that changed:

```bash
# 1. The kiosk walks end to end and the read-back carries the whole intake.
open http://localhost:3000/kiosk

# 2. The board names the department, the doctors on duty and the patient.
curl -s http://localhost:8000/queue/board | python3 -m json.tool

# 3. The doctor console shows one encounter with one next step.
open http://localhost:3000/doctor
```

If you want the seeded morning back for a demo:

```bash
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  .venv/bin/python -m scripts.seed_doctor_demo
```

## AWS / Ubuntu host

Follow `docs/18-AWS-UBUNTU-QUICKSTART.md` as written; the only addition is the
reseed, because two trees changed version:

```bash
git pull --ff-only
docker compose build api web        # both, for the reasons above
docker compose up -d --wait
docker compose exec api python -m app.seed
curl -fsS http://localhost:8000/health
```

`app.seed` is idempotent. It republishes a tree when the authored version is
higher than the stored one, which is exactly the gynae and general-medicine case.

---

## The five-minute demo script

Run it once before showing anyone. It exercises every changed surface and it is
the order a prospective user will ask about.

1. **Kiosk, Hindi.** Language → "मैं अपने लिए" → fill name / age / gender /
   phone. Watch the left rail fill in as you type.
2. Chief complaint: say or type **"माहवारी अनियमित है"** → Gynecology.
3. Answer the questions. Note that the mic only appears on the **last two**, and
   that a menstrual complaint never gets asked about soaking a pad in an hour.
4. **Read-back**: the rail is still there, and the summary now repeats every
   question and your own closing words — in Hindi.
5. Confirm → token → **Print slip** (the slip now carries the name).
6. **Board** (`/board` on the hall screen): department, doctors on duty, the
   token being served with its patient, and the named next three.
7. **Doctor** (`/doctor`, sign in as the seeded doctor): the encounter bar says
   where you are in words, one filled button says what to do next. Complete a
   consult, press `N`, watch the rail follow.

## What to say if asked

- Patient names now appear on the public board. That is deliberate and matches
  what a government OPD board already does — but no clinical reason, complaint or
  red-flag text ever appears there.
- The gynae and general-medicine question changes are **structurally validated
  and versioned, not clinically reviewed**. Do not present them as signed off.
- The Marathi and Telugu strings on the new screens are model-drafted and still
  await native review, like the rest of the bank.
