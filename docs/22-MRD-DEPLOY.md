# 22 — Deploying MRD (record scanning and the doctor's Reports tab)

Sessions **MRD1** and **MRD2** together, because MRD1 was never deployed: the
capture surface, the extraction pipeline and the doctor's Reports tab go to a
box as one release.

The mechanics of a deploy — how to reach each host, the container swap, the
rollback — are **doc 20 §1–§4** and are not repeated here. This note is only
what is different about this release, and there are four things:

1. it needs a **persistent, shared volume** that no previous release had;
2. the backup and restore scripts changed to carry that volume — the database
   dump alone stopped being a complete restore the moment MRD1 shipped;
3. it has **four pending migrations**, none of which has run on either box;
4. extraction needs a **vision-capable LLM**, and refuses rather than guessing
   when it does not have one.

Take the release SHA from the remote, not from this page:

```bash
git fetch origin && git rev-parse origin/main
```

---

## 1. The volume, and the bug it fixes

`OBJECT_STORE_DIR` defaults to `/data/records`. Until this release the compose
file mounted **nothing** there, which was wrong in two independent ways:

- The api writes the pages and the **worker** reads them. They are separate
  containers, so an unshared directory meant every document the extraction sweep
  claimed failed with its pages missing. Only the api's own post-upload nudge
  ever worked, and only until that container was replaced.
- `make deploy` recreates containers. A container-local directory takes every
  scanned report with it, leaving rows in Postgres pointing at bytes that no
  longer exist.

`docker-compose.yml` now declares a named `recordsdata` volume and mounts it on
`api`, `worker` and `beat`. **Nothing needs doing by hand**, but check it landed
before the first scan:

```bash
docker compose config | grep -A2 recordsdata      # api, worker, beat
docker compose exec api sh -c 'touch /data/records/.probe && ls -la /data/records'
docker compose exec worker sh -c 'ls -la /data/records/.probe'   # the same file
```

If the second command cannot see the file the first created, the volume is not
shared and extraction will fail on every document — stop and fix that first.

## 2. The pages are in the backup, and the drill proves it

Until this release the 15-minute backup was `pg_dump` and nothing else, so a
restore brought back every `MedicalDocument` row and every extraction pointing
at page bytes that were gone. That failed *visibly* — the page route answers
`410` and the Reports tab renders it as its own state — but a visibly missing
biopsy report is still a missing biopsy report.

Both backup scripts (`deploy/aws/backup.sh`, `deploy/omen/cloud-backup.sh`) now
sync the pages to `s3://$BACKUP_BUCKET/pages/`, `restore.sh` syncs them back,
and the daily drill checks they are really there.

**Why a sync and not a tarball.** Page keys are deterministic — one per
(patient, document, page), built in exactly one place (`mrd.page_key`) — nothing
rewrites a key, and no code path deletes one. The store is append-only in
practice, so `aws s3 sync` uploads only what is new. A 15-minute tar of a
directory growing by gigabytes a week would be unusable inside a month, and a
per-backup copy would multiply those gigabytes for bytes that never change.

There is deliberately **no `--delete`**, on either direction. A restore of an
older database must still find its pages, and nothing should be able to remove a
patient's scanned report from the backup as a side effect of a sync.

**The ordering is the correctness argument.** The database is dumped *first* and
the pages synced *second*. Because pages are append-only, every page the dump
references already existed when the dump was taken, so a sync that runs
afterwards is guaranteed to contain it. The reverse order loses exactly the
report a coordinator scanned during the backup — the most likely to matter and
the least likely to be noticed. `deploy/aws/test-contract.sh` asserts the line
order in both scripts, because a comment saying so is not a test.

Pages uploaded *between* the dump and the sync are harmless: they are orphans no
restored row references, and the next dump adopts them.

**What the daily drill now checks.** `verify-restore.sh` already restored each
backup into an isolated database. It now also reads the object keys of the most
recent documents out of that restored database and confirms each one is present
in the bucket:

```
pages checked: 50 (all present)
```

The manifest records `pages_checked`, so "verified" on a backup holding scanned
documents cannot be confused with "verified" on one that holds none. If any
sampled page is missing the drill **fails** with the key it could not find —
that is the check whose absence made the old drill able to pass on a box whose
scanned reports were gone. It samples the newest keys
(`RECORDS_VERIFY_SAMPLE`, default 50) rather than all of them: the store grows
without limit, the drill runs daily, and an ordering bug shows up in the newest
page first.

**The manual failover drill** (`drill-report.py`) now requires
`known_document_id` and refuses to finalize unless
`document_pages_readable_on_target` is true. A `MedicalDocument` row restores
from the dump whether or not its photographs came with it, so asserting the row
proves nothing — the drill has to *open the pages* on the target.

### Still owed

The scripts are written and unit-asserted, and **neither has run against a real
bucket on either box.** The first real backup after this deploy is the test;
watch it, then run `verify-restore.sh` by hand and confirm the `pages checked`
line is non-zero before trusting the schedule.

Bucket growth is also still unmanaged — `pages/` has no lifecycle policy, and
doc 21 §8.7 (storage growth) applies to the backup copy as much as to the disk.

## 3. Migrations

Four, and **none has ever run on either box**. `make deploy` does not run
migrations (doc 20 §2), so this is a deliberate step.

| Revision | What it adds |
|---|---|
| `c6e3681f5ce1` | `patients.external_id`, `visits.candidate_patient_id` (AR1) |
| `520d07f0b3e4` | `users.kiosk_pin_hash` + lockout columns (AR3) |
| `c063fd91e198` | `visits.rx_mode` / `conclusion_note` / `concluded_at` (Session C) |
| `efb79a43afb3` | `medical_documents` + `document_extractions` (MRD1) |

All four are **additive with no backfill**, so migrate first and swap containers
after — the running code tolerates the new schema.

```bash
docker compose --profile migration run --rm migrate      # AWS: deploy.sh does this
docker compose exec api alembic current                  # expect efb79a43afb3
```

## 4. Configuration

```bash
OBJECT_STORE=filesystem
OBJECT_STORE_DIR=/data/records
MRD_ENABLED=true
```

`MRD_ENABLED=false` is a real, safe operating mode and worth knowing: pages are
still captured, stored and shown to the doctor, and only the machine reading is
absent. Turn it off if the vision vendor is unavailable or the bill needs
stopping — the coordinator's job and the doctor's page viewer both keep working.

**Extraction needs a vision-capable `LLM_PROVIDER`** (`gemini` or `openai`).
Sarvam and the local vLLM declare `supports_images = False` and raise
`UnsupportedCapability` *before* they are dialled, which lands the document in a
visible `extraction_failed` with the pages still viewable.

> Do not "fix" that by stripping the images. A model asked to summarise pages it
> was never shown produces something that reads exactly like a real reading of a
> lab report.

Because `UnsupportedCapability` subclasses `ProviderUnavailable`, a chain walks
*past* a text-only primary to a vision model — so `LLM_PROVIDER=local_vllm` with
`LLM_FALLBACK_PROVIDER=gemini` is a sane pilot posture: everything else stays on
the box, and only document pages go out.

## 5. What to check after the swap

```bash
curl -fsS https://<host>/health
```

Then, from the desk phone and the console:

1. `/scan` — pick a patient, photograph one page, Done. The page count must be
   the server's, not the phone's.
2. Within a minute or two, open that patient in `/doctor`. The spine's
   **Reports** line should state what is on file *before* any tab is opened.
3. Open the tab. The reading must be badged **unverified** until *Mark reviewed*
   is tapped.
4. Tap a value's page number — the original photograph must open. If it does
   not, check §1 before anything else.
5. Back on `/scan`, the **Could not be read** section lists anything the model
   refused, with a re-read. On a healthy box it should be empty.

Then prove the backup, once, by hand — this is the first release where that is
not the same thing as proving the database backup:

```bash
/opt/opd/current/deploy/aws/backup.sh                 # note the BACKUP_ID
aws s3 ls "s3://$BACKUP_BUCKET/pages/" --recursive --summarize | tail -3
/opt/opd/current/deploy/aws/verify-restore.sh         # expect: pages checked: N (all present)
```

`N` must be **non-zero** after step 1 has scanned something. A zero there means
the drill is passing without checking anything, which is the state this release
exists to end.

## 6. Rolling back

The code rolls back the doc 20 way. **The migrations do not need to** — they are
additive, and the previous release runs against this schema unchanged. Do not
downgrade `efb79a43afb3` to roll back application code; that would drop every
scanned document's row while its pages sat in the volume.

If MRD itself is the problem, set `MRD_ENABLED=false` and recreate the api
rather than rolling the release back. Capture and viewing survive; only the
automatic reading stops.
