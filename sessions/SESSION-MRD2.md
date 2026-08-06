# SESSION-MRD2 — Medical record digitisation: the doctor's surface

Plan: `sessions/SESSION-CLINICAL-INTEL-PLAN.md` §1.5 + §6 (session M2).
Design doc updated this session: `docs/21-MEDICAL-RECORD-DIGITISATION.md` §1.5,
§1.6. Deploy note written this session: `docs/22-MRD-DEPLOY.md`.
Branch: `main`. Baseline at start: backend 1,553 green.

## What this session was for

M1 built the capture surface and the pipeline and deliberately stopped: no
screen showed a doctor any of it. This session is that screen, plus the desk
screen that M1's `retry` endpoint never had.

## Acceptance criteria, restated and checked

- [x] `Reports` joins the tab rail and the "Coming soon" disclosure graduates
      the entry it was built to hand over.
- [x] Summary card badged **AI-read, unverified** until *Mark reviewed*.
- [x] Flagged-values table: value, unit, range, **which range decided it**, and
      a page link to the original.
- [x] Original page images, fetched under the auth guard, revoked on unmount.
- [x] Every failure state visible: waiting, being read, could-not-be-read with
      its reason and its pages, and a page missing from the store.
- [x] Spine slot stating what is on file before any tab is opened.
- [x] Coordinator retry surface for `extraction_failed`.
- [x] Doc 20-style deploy note.
- [x] Gates: backend **1,560**, reports E2E **7** against a live stack,
      production build, typecheck, lint all green. Conformance untouched.

Not in scope and not done: extracted values feeding anything but display; an
allergy field; the backup job.

## The decisions worth knowing about

**The spine got a fifth slot, against its own written rule.** `ContextSpine`
says in its header that anything wanting a fifth permanent slot is asking for
the spine to stop being readable — and the plan asks for exactly that. The plan
wins here because the module's whole stated intent is that the doctor knows
*before* the patient is in the room, and a badge on a tab nobody has opened does
not achieve it. The cost is contained: one line that never wraps, a link into
the tab rather than content of its own, amber at its loudest, and the argument
written into the file so the sixth request gets refused on the record.

**"Awaiting review", not "new".** The plan's wording was `Reports: 2 new`.
`verified` is a fact about the *reading*, not about this doctor having seen it,
so "new" would be a claim the data cannot make to the second doctor who opens
the same patient. The counts say what they know.

**Page bytes cannot be an `<img src>`, and that is the point.** The route is
guarded and the staff token lives in `localStorage`, so the only thing that
would make the tag work is a signed URL — which §1.3 refuses precisely because
it outlives the session that minted it. `PageViewer` fetches with the bearer
token and revokes the object URL on unmount; a console left open on a ward
machine all morning must not hold every page of every patient it has shown.

**The desk's failure list is not a `DocumentOut`.** It has no `extraction`
field and must never grow one. A coordinator is not `require_clinical`; telling
them the machine failed on pages they photographed must not become a way to
browse the reading `require_clinical` keeps from them. Pinned by
`test_the_failure_list_carries_no_reading_at_all`.

**The fake needed a canned MRD reply.** M1 shipped the pipeline with none, so
the fake answered "ok" to a strict-JSON contract, the parse failed, and `make
dev` could only ever demonstrate `extraction_failed` — the Reports tab was
undemonstrable without a vendor key. The fixture is held to the module's own
rule and tested for it: **no `flag` on any row**.

## The bug this session found in M1

**The scanned pages had nowhere to live.** `OBJECT_STORE_DIR=/data/records` was
the default in `.env.example` and neither compose file mounted anything there.
Two independent failures, both silent:

- the api writes the pages and the **worker** reads them during extraction —
  different containers, so an unshared directory means every document the sweep
  claims fails with its pages missing, and only the api's own post-upload nudge
  ever worked;
- `make deploy` recreates containers, which would have taken every scanned
  report with it, leaving rows pointing at bytes that no longer existed.

Fixed on both compose files (a named volume locally, a `/data/records` host bind
on AWS to match postgres and redis so the backup scripts can reach it),
`infra/user_data.sh` creates it, and `deploy/aws/test-contract.sh` now fails if
it is missing from the rendered config.

## The doc 04 §5 self-critique, and what it changed

Three things the screenshots showed that reading the code did not:

1. **The tab printed the spine's own sentence.** "2 documents on file · 4 values
   flagged · 2 awaiting your review" at the top of the tab, forty pixels under a
   spine line stating the same three counts. Removed; the spine never unmounts,
   so it is the right place for them.
2. **The values screenshot was a picture of the screen above the values.**
   `scrollIntoViewIfNeeded` put the table under the sticky spine. The spec now
   scrolls and backs off.
3. **`/scan` was being critiqued at desktop width.** It is a phone in someone's
   hand; that describe block now pins the same 414×896 viewport the `scan`
   project uses.

And one copy fix: the stored failure reason is an operator's phrase ("could not
be read by the model: gemini http 503"), so running it on after "The machine
could not read these pages" made the screen say it twice. It is its own line
now.

The deliberate aesthetic risk (one per surface): the **range track** — a flagged
value drawn on the interval its own report printed, so "slightly low" and "a
third of the floor" stop looking alike. It refuses rather than approximates: no
low, no high, or a value that is not a plain number, and nothing is drawn.

## Evidence

- `make test-backend` → **1,560 passed** (1,553 → +6 in `test_records_routes`
  for the failure list, +1 in `test_mrd_contract` for the demo fixture).
- `npx playwright test --project=reports` → **7 passed** against a live stack
  (api on :8123 with `MRD_ENABLED=true` + `OBJECT_STORE=filesystem`, web dev
  server on :3210, `scripts.seed_doctor_demo`).
- `npm run build` / `tsc` / `eslint` clean. `/doctor` is 30.9 kB.
- `docker compose config` valid on both files; `bash -n` on the changed scripts.
- Screenshots: `web/screenshots/mrd2/01…07`, self-critiqued above.

## Migration

**None.** M2 added no schema. The four migrations pending on Omen are unchanged
from the last handoff — `c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`,
`efb79a43afb3` — and doc 22 §3 is how they get applied.

## Debt this session created or inherited

1. **The backup still does not include the pages directory.** M2 gave it a real
   volume; backing it up is doc 22 §2 and remains unstarted, restore side
   unexercised. Still the largest debt in the module.
2. **`seeds/lab_reference_ranges.json` still unreviewed.** The UI now keys off
   it exactly as intended — those rows read `our range` and carry a note — so
   the flag is doing its job, and the oncologist review is still owed.
3. **Verification is per-reading, not per-doctor.** A second doctor opening the
   same patient sees a reading a colleague reviewed, correctly attributed but
   not re-asked. That is deliberate; if it ever needs to be per-doctor, the
   counts change meaning too.
4. **No re-scan from the doctor's side.** The failed-document copy tells them to
   ask the desk. A doctor cannot trigger a re-read, only the desk can.
