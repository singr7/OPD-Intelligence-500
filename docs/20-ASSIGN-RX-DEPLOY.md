# 20 — Deploying Sessions A, B and C (assignment, identity, consult paths)

Release commit: **the head of `main`**. `4c7f1fb1e50e2e490f3d30b224e9b4ba4636c857`
is the last *code* commit of this release (23 commits: AR1–AR3, Session B,
Session C); anything after it on `main` is documentation only. Take the SHA from
the remote rather than from this page, which cannot contain its own:

```bash
git fetch origin && git rev-parse origin/main
```

Unlike S-UX.6 (doc 19), this release **has migrations** — three of them, and none
has ever run on either box:

| Revision | From | What it adds |
|---|---|---|
| `c6e3681f5ce1` | `a4d5e6f7b801` | `patients.external_id` / `external_id_kind`, `visits.candidate_patient_id` / `patient_link_state` |
| `520d07f0b3e4` | `c6e3681f5ce1` | `users.kiosk_pin_hash` and its lockout columns |
| `c063fd91e198` | `520d07f0b3e4` | `visits.rx_mode` / `conclusion_note` / `concluded_at` / `concluded_by` |

All three are **additive and nullable with no backfill**, so the old code keeps
running against the new schema. That is what makes "migrate first, then swap
containers" safe here.

There is **no new tree version and no new Python or npm dependency**; `make
preflight` was run against this commit and both images import.

---

## 0. Before you touch a box (laptop)

```bash
git checkout main && git pull --ff-only
git rev-parse HEAD          # must print 4c7f1fb1e50e2e490f3d30b224e9b4ba4636c857
make test-backend           # 1376 passed
make preflight              # both images build and import
```

---

## 1. AWS — a host already in disposable no-PHI test mode

This is the path to run **first**, because disposable mode is writable and the
only place these three sessions can be exercised end to end without touching a
real patient record.

`deploy.sh` runs the migration profile itself (`compose --profile migration run
--rm migrate`), so there is no separate alembic step on this host.

```bash
export RELEASE_SHA=4c7f1fb1e50e2e490f3d30b224e9b4ba4636c857

# 0. Record where you are, so rollback has a target
sudo cat /opt/opd/runtime/releases/current-sha
sudo cat /opt/opd/runtime/releases/disposable-test-active   # expect mode=disposable-no-phi

# 1. Pin the new commit (as the repo owner, not root)
cd /opt/opd/source/repo
git fetch origin
git checkout --detach "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git status --porcelain)"

# 2. Build the CPU-only images for this SHA
sudo /opt/opd/current/deploy/aws/build-local-release.sh "$RELEASE_SHA"

# 3. Deploy. This migrates (all three revisions) and keeps writer state.
sudo /opt/opd/current/deploy/aws/deploy.sh "$RELEASE_SHA"

# 4. Refresh disposable mode: re-seeds, re-verifies OTP echo, keeps ENV=test
sudo /opt/opd/current/deploy/aws/activate-disposable-test.sh \
  "$RELEASE_SHA" --confirm-no-phi
```

`deploy.sh` ends with `writer=off`. **`off` means writable** — it reports
PostgreSQL's `default_transaction_read_only`, so `off` is correct for disposable
mode and `on` means it fell back to read-only standby.

### Confirm the schema actually moved

```bash
sudo docker compose \
  --env-file /opt/opd/runtime/application.env \
  --env-file /opt/opd/runtime/writer.env \
  --env-file /opt/opd/runtime/release.env \
  -f /opt/opd/current/deploy/aws/compose.yml \
  exec -T postgres psql -U opd -d opd -tAc "select version_num from alembic_version"
#   -> c063fd91e198

curl -i https://opd-cloud.radpretation.ai/api/health
curl -fsS https://opd-cloud.radpretation.ai/api/environment | python3 -m json.tool
#   environment_id: aws, and the deployed full SHA
```

### Seed the doctor's morning for the end-to-end test

`activate-disposable-test.sh` runs `app.seed` and `seed_app_demo`, but not the
doctor demo — and Sessions B and C are all doctor-console work.

```bash
cd /opt/opd/current
sudo docker compose \
  --env-file /opt/opd/runtime/application.env \
  --env-file /opt/opd/runtime/writer.env \
  --env-file /opt/opd/runtime/release.env \
  -f deploy/aws/compose.yml \
  exec -T api python -m scripts.seed_doctor_demo
```

It prints the five walk-ins, leaves Sita Kumari unassigned in the department
pool, and puts token 12 in the room. Login is `+915550001001`; disposable mode
sets `OTP_DEBUG_ECHO=true`, so the code appears on the sign-in screen.

**The seeded kiosk PIN is `4729`** on this host, because disposable mode writes
`ENV=test` and `app.seed` only plants the committed PIN when `is_local` is true.
That is exactly why this mode must never see PHI, and why a promoted box gets its
PIN from `make kiosk-pin` typed by a human.

---

## 2. AWS — the end-to-end script for these three sessions

Roughly ten minutes, in the order the work was built. Every step is something
that did not exist before this release.

**Session A — identity and assignment (kiosk)**

1. `https://opd-cloud.radpretation.ai/kiosk` → language → **"Have you visited us
   before?"** → *Yes* → type a phone number that exists (a seeded patient's).
2. The kiosk must say only *"we may already have your file"* — **no name, no MRN,
   no history**. If it shows any identifying detail, stop and report it; that is
   the one thing AR3 exists to prevent.
3. Finish the intake to the token screen. The staff strip below is **locked**.
4. `Unlock` → PIN `4729` → the strip names the candidate patient, the routed
   department, and the on-duty doctor. Press `Confirm`.
5. Change the department before confirming on a second run: the token is
   **reissued** in the new department's series and announced.

**Session B — the worklist and the spine (doctor console)**

6. `https://opd-cloud.radpretation.ai/doctor` → sign in as `+915550001001`.
7. The rail defaults to **Mine**; `Unassigned` shows **1** with a marigold
   "1 waiting with no doctor" line while its tab is closed. Sita Kumari is not in
   Mine.
8. Open `Unassigned` → **Take this patient** → she opens on the stage and the
   count drops to 0.
9. Move through Overview / Intake answers / History / Consult: the context spine
   (name, token 12, diagnosis line, allergies line, red-flag stamp) **never
   disappears**, including while the consult note is open.

**Session C — the consult and prescription paths**

10. On the patient in the room, press `D`. The four steps read
    `1 Capture — you are here · 2 Review · 3 Sign · 4 Prescription`.
11. **Type note** (no microphone needed) → *Add a medicine* → `Tab Augmentin 625`,
    frequency `BD`, duration `5 days` → *Add*. Fill **Impression**.
12. Check that there is **no Print button anywhere** and the sign bar says the
    prescription is produced by the signature.
13. **Sign this note** → the prescription panel appears with Print, Download PDF,
    WhatsApp and SMS. That is a prescription produced with no speech at all.
14. Add a second medicine that is not on the formulary (e.g. `Tab Notarealdrug
    10`) on another patient: the signature must be **refused by name** until you
    acknowledge it, and acknowledging must calm it to marigold rather than clear
    it.
15. On a third patient, `D` → **More** → **Conclude without a system note** →
    *Written on paper* → the dialog must name the pharmacy, the patient's app and
    the follow-up reminders → *End the consult*. The patient leaves the worklist.
16. Read it back:

```bash
curl -fsS -H "Authorization: Bearer $TOKEN" \
  https://opd-cloud.radpretation.ai/api/doctor/patients/<visit_id> |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["rx_mode"], d["conclusion_note"], d["entry_state"])'
#   -> external_manual  Written on the OPD pad.  done
```

**Dictation** is worth one pass if the box has a microphone path: `Dictate` shows
an elapsed timer and, where the browser gives an analyser, real level bars —
never a decorative waveform. `Stop & transcribe` is green.

---

## 3. Omen — after AWS passes

Omen is the pilot box with real patients on it, so it goes second. The shape is
doc 13; the difference from every previous Omen upgrade is that **`make deploy`
does not migrate** and this release has three revisions.

```bash
cd ~/projects/opd

# 1. Checkpoint. This is what makes everything below reversible.
git fetch origin
./deploy/omen-checkpoint.sh          # write the stamp down

# 2. Pull and build without switching anything over
git pull --ff-only origin main
git rev-parse HEAD                   # 4c7f1fb1e50e2e490f3d30b224e9b4ba4636c857
docker compose config | grep NEXT_PUBLIC   # must be https://opd.radpretation.ai/api
docker compose build                 # 5–15 min; stop opd-stt first if the web build OOMs

# 3. Migrate — schema first; the additive columns leave the old code happy
docker compose run --rm \
  -e ALEMBIC_DATABASE_URL=postgresql+asyncpg://opd:${POSTGRES_PASSWORD:-opd_local_dev}@postgres:5432/opd \
  api alembic upgrade head
docker compose exec -T postgres psql -U opd -d opd -tAc "select version_num from alembic_version"
#   -> c063fd91e198

# 4. Swap containers. `up -d`, NEVER `down` (it detaches opd-vllm / opd-stt).
docker compose up -d
docker compose ps
```

Verify in doc 13 §7's order: `curl -s localhost:18080/health`, the two GPU
containers still `Up` and reachable on `opd_default`, then **the kiosk in a
browser** — it is the only test that really matters on this box.

### The one manual step Omen needs

Omen is not `is_local`, so `app.seed` **refuses** to plant a kiosk PIN there and
logs a warning. Without a PIN the staff strip cannot be unlocked and Session A's
assignment never happens.

```bash
docker compose exec api python -m scripts.set_kiosk_pin            # who has one
docker compose exec -it api python -m scripts.set_kiosk_pin \
  --phone +915550000002 --set                                      # prompts; never echoes
```

Use the real coordinator's phone number, not the seeded one, if the pilot has its
own staff row. `--clear` and `--unlock` are the forgot-it and locked-out paths.

Do **not** run `make seed` or `make slots` on Omen: no tree version changed and
the box has its own data.

### Rollback

```bash
# AWS — code only; data and writer state untouched, refuses if images were pruned
sudo /opt/opd/current/deploy/aws/rollback.sh <previous-sha>

# Omen — doc 13 §7, using the checkpoint stamp you wrote down
./deploy/omen-rollback.sh <stamp>
```

Both roll **code** back. The three migrations are additive, so the previous code
runs against the migrated schema without a schema downgrade — which is the point
of shipping them additive. Do not run `alembic downgrade` to undo a bad deploy.

---

## 4. What to say if asked

- **Nothing captures an allergy yet.** The context spine has a permanent slot for
  it and says, in words, that the system does not record one. It must never be
  read as "no known allergies".
- **The arrival screens are English + Hindi.** Marathi and Telugu fall through to
  English rather than being machine-translated, and are pending native review.
- **`4729` is a committed, world-readable PIN.** It is planted only where
  `ENV` is `local` or `test` — which includes the AWS disposable box, and is one
  more reason that box must never receive real patient data.
- **A conclusion cannot be undone and a signed note cannot be amended.** Both are
  in the audit trail and on no screen.
- **The recording level meter has never met a real microphone** — headless
  Chromium has none. Where no analyser exists the console deliberately shows an
  elapsed timer and no bars rather than a decorative waveform.
