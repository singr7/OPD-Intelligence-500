# seeds

Reference data for the pilot hospital, loaded by `backend/app/seed.py`
(`make seed`). Data lives here as JSON — not as Python literals — so the
non-technical edits it will attract (a doctor's phone, a department name) are a
data change, and so the admin console (S18) can read and write the same files.

## Files

| File | What |
|---|---|
| `hospital.json` | The pilot hospital + its departments (doc 03 §3). |
| `doctors.json` | Seed doctors and their login users. |

Patients are **generated**, not listed: 50 fake patients from a fixed Faker seed
(`--patients N` to change the count). Fixed seed ⇒ the same 50 patients, with
the same MRNs, on every machine — so a bug reproduces from a session log.

## Idempotency

Every entity has a natural key — hospital `code`, department `(hospital, code)`,
doctor `reg_no`, user `phone`, patient `mrn`. The loader looks up by that key
and updates in place, so `make seed` twice is the same as once. This matters
beyond tidiness: seeding is how the demo box gets rebuilt, and a loader that
duplicates on re-run turns a rebuild into a data-cleanup job.

Re-running after an edit here *updates* the existing rows rather than inserting
new ones, and each such update is audited like any other clinical write
(actor `seed`).

## Not seeded here

Question trees (S4 authors the bank), price book (S3), protocol templates (S17).
