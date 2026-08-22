# seeds

Reference data for the pilot hospital, loaded by `backend/app/seed.py`
(`make seed`). Data lives here as JSON — not as Python literals — so the
non-technical edits it will attract (a doctor's phone, a department name) are a
data change, and so the admin console (S18) can read and write the same files.

## Files

| File | What |
|---|---|
| `hospital.json` | The pilot hospital + its departments (doc 03 §3), each with its `care_system` (doc 24 §3). |
| `doctors.json` | Seed doctors and their login users. |
| `formulary.json` | What a dictated drug name is checked against — **two shelves**, see below. |

Patients are **generated**, not listed: 50 fake patients from a fixed Faker seed
(`--patients N` to change the count). Fixed seed ⇒ the same 50 patients, with
the same MRNs, on every machine — so a bug reproduces from a session log.

### `formulary.json` has two shelves

Each entry carries a `scope` — a system of medicine — and an entry with no
`scope` is `allopathy`, which is why the 189 oncology generics were not re-tagged
by hand when the field was added. `get_formulary(scope=...)` loads **one** shelf,
so a department's `capabilities.formulary_scope` decides which preparations may
be called known, in both directions: an ayurvedic churna is not flagged during an
ayurveda consult, and a cytotoxic is not dictatable in one.

A `scope` that is not a system of medicine **raises** rather than being filed
somewhere nobody searches — where it would read as "not in formulary" forever,
which on screen looks exactly like a doctor mis-dictating a name.

The 89 `scope: ayurveda` entries are **model-drafted and UNREVIEWED**, as the
file's own `_comment_ayurveda` says. A BAMS practitioner must review them before
the module is enabled for real patients — the same gate the ayurveda trees and
the oncology tree bank sit behind (doc 24 §9). `scope` is a filing decision, not
a clinical claim: nothing in this file is a dose, an indication or an anupana.

## Idempotency

Every entity has a natural key — hospital `code`, department `(hospital, code)`,
doctor `reg_no`, user `phone`, patient `mrn`. The loader looks up by that key, so
`make seed` twice is the same as once. This matters beyond tidiness: seeding is
how the demo box gets rebuilt, and a loader that duplicates on re-run turns a
rebuild into a data-cleanup job.

## These files describe a box nobody has set up yet

For the rows a person can edit from the admin console — the **hospital**, its
**departments**, staff **users**, **doctors** and **clinic templates** — the
loader **creates what is missing and never overwrites what it finds**.

Adding a department or a doctor to a file here and re-running is still how new
reference data reaches a box. Editing one that is already there changes nothing
on a box where somebody has already set it up, because the console is the
authority on those rows: `PATCH /admin/hospital` and the department editor
(SESSION-AYUR-1), staff onboarding and deactivation (S-GL.2). Overwriting them
would have reverted a hospital renamed in the console — taking the prescription
letterhead and every intake pass back with it — reopened a department an
administrator had closed, and reactivated a doctor they had retired.

A row left alone *because it differs* from the file is reported as `kept` at the
end of the run, so the operator can see the loader noticed and stood down. **The
files are validated on every run whether or not they are written**, so a typo in
an existing department's `care_system` still fails loudly.

Patients are exempt — generated demo data, and no console edits them. So are the
price book, the tree bank and the protocol bank, which are versioned or
append-only content with editors of their own.

**If you want a file here to win, change the row in the console too, or delete
it and re-seed.**

## Not seeded here

Question trees (S4 authors the bank), price book (S3), protocol templates (S17).
