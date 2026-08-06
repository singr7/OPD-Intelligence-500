# 22 — Deploying MRD (record scanning and the doctor's Reports tab)

Sessions **MRD1** and **MRD2** together, because MRD1 was never deployed: the
capture surface, the extraction pipeline and the doctor's Reports tab go to a
box as one release.

The mechanics of a deploy — how to reach each host, the container swap, the
rollback — are **doc 20 §1–§4** and are not repeated here. This note is only
what is different about this release, and there are four things:

1. it needs a **persistent, shared volume** that no previous release had;
2. that volume is **not covered by the database backup**, which changes what a
   restore means;
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

## 2. The backup is no longer complete, and this is the largest debt in the release

The 15-minute Postgres backup does **not** include the pages. A restore from
database alone brings back every `MedicalDocument` row and every extraction,
pointing at page bytes that are gone.

That failure is at least *visible* — the page route answers `410` with a
sentence rather than a broken image, and the doctor's tab says the page is no
longer stored and that an operator needs to know. But visible is not the same as
survivable, and **the operator work is unstarted.**

Until the backup job includes the volume, be explicit with the hospital that a
restore recovers the readings and not the photographs. Adding it is roughly:

```bash
# alongside the existing pg_dump, per the doc 17/18 backup runbook
docker run --rm -v opd-intelligence-alwar_recordsdata:/src:ro -v "$BACKUP_DIR":/out \
  alpine tar czf /out/records-$(date -u +%Y%m%dT%H%M%SZ).tar.gz -C /src .
```

That is a sketch, not a tested runbook step. It has not been run on either box,
and the restore side has not been exercised at all.

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

## 6. Rolling back

The code rolls back the doc 20 way. **The migrations do not need to** — they are
additive, and the previous release runs against this schema unchanged. Do not
downgrade `efb79a43afb3` to roll back application code; that would drop every
scanned document's row while its pages sat in the volume.

If MRD itself is the problem, set `MRD_ENABLED=false` and recreate the api
rather than rolling the release back. Capture and viewing survive; only the
automatic reading stops.
