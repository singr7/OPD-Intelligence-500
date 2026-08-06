# HANDOFF — after SESSION-MRD2

**Repo state:** branch `main`. `make test-backend` **1,560 passed**. Reports E2E
7 passed against a live stack, `npm run build` / `tsc` / `eslint` clean. **M2
added no migration.** The four pending on Omen are unchanged —
`c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3` — all applied
locally only, and `make deploy` does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands:** the first of the four Clinical Intelligence modules
(`sessions/SESSION-CLINICAL-INTEL-PLAN.md`) is **complete**. A coordinator
photographs a patient's reports at the desk; the pipeline reads them, flags the
values in Python and writes a short summary; the doctor's console states what is
on file before they open anything, shows the reading as a draft until they
review it, and puts the original photograph one tap from every number. The desk
is told what failed to read and can ask for a re-read. Design doc:
`docs/21-MEDICAL-RECORD-DIGITISATION.md`. **Deploying it is `docs/22-MRD-DEPLOY.md`
— read that before touching a box, it is not a normal release.**

## Next session — M3 (PACS stub), and it has a plan

`sessions/SESSION-CLINICAL-INTEL-PLAN.md` §2 + §6 "Session M3": config, one
proxy endpoint, a fake DICOMweb server, a spine `Images (n)` slot and a viewer
popup handoff. It needs nothing from M1/M2.

**It has an external gate that is worth resolving first** (plan §8.1): the
imaging centre must register studies under the UHC ID (`Patient.external_id`) as
the DICOM `PatientID`. Until that operational agreement exists, study lookup
returns empty for every patient and the module cannot be acceptance-tested
against the real `RAD-RENVA-PACS`. If it is unresolved, **M4 (ambient notes,
plan §3) is the better next session** — it builds on the Session C dictation
stack that already ships and has no external dependency.

Note for M3: the spine now has five slots and the sixth is `Images (n)`. Read
the argument in `ContextSpine.tsx`'s header before adding it — the case for the
Reports line was that the doctor must know *before* the patient is in the room,
and Images has the same claim, but that is now two exceptions to a rule with
four items in it. Consider whether Reports and Images should share one line.

The other candidates are unchanged: allergy capture, deploying the pending
migrations to Omen, and a correction path for a signed note or a concluded
consult.

## Watch out for

- **The page backup is written but has never run for real.** M2 gave
  `/data/records` a real volume on both compose files (M1 had mounted *nothing*
  there), then taught the backup, restore and drill scripts to carry it. The
  first backup after the next deploy is the test — doc 22 §5 has the three
  commands, and `pages checked: N` must be **non-zero** once something has been
  scanned. A zero means the drill is passing without checking anything.
- **Dump before sync, and never `--delete`.** Pages are append-only, so a page
  sync taken *after* the `pg_dump` necessarily contains every page the dump
  references; the reverse order drops precisely the report scanned during the
  backup. `deploy/aws/test-contract.sh` asserts the line order in both backup
  scripts and refuses a `--delete` on any of the three syncs. If you touch those
  scripts, that test is the thing that will tell you why.
- **The extraction contract has no flag field, and that is load-bearing.** A
  model may read a number; deciding it is abnormal is `app/mrd/ranges.py`, in
  Python, on `Decimal`. Two guards now:
  `test_a_flag_in_the_models_reply_is_ignored_entirely` and
  `test_the_demo_fixture_parses_and_volunteers_no_flag` — the fake's canned
  reply is an input to the contract and is pinned like one.
- **`seeds/lab_reference_ranges.json` ships `review_pending`, and the UI now
  keys off it.** Rows flagged from that table read `our range` and carry a note;
  rows from a range the lab printed read `printed on report`. Flipping `status`
  to `reviewed` without an oncologist actually reviewing it silently promotes
  every one of them.
- **`/records/scan/failures` must never grow an `extraction` field.** A
  coordinator is not `require_clinical`; being told the machine failed must not
  become a way to browse the reading. Pinned by
  `test_the_failure_list_carries_no_reading_at_all`.
- **Page images are fetched, not `src`-ed.** The route is guarded and the token
  is in `localStorage`. Do not "simplify" `PageViewer` into an `<img src>` — it
  would need a signed URL, which doc 21 §1.3 refuses on purpose. And keep the
  `revokeObjectURL` on unmount.
- **Extraction needs a vision-capable `LLM_PROVIDER`.** Sarvam and the local
  vLLM declare `supports_images = False` and raise `UnsupportedCapability`
  *before* being dialled. Do not "fix" that by stripping the images — a summary
  of pages the model never saw reads exactly like a real one. A fallback chain
  walks past a text-only primary, so `local_vllm` + `gemini` fallback works.
- **The doctor console's token key is `opd_staff_token`**, shared with the
  coordinator console — not a doctor-specific key. Cost half an E2E run.
- **Tests default `MRD_ENABLED=false`** (conftest). The post-upload nudge builds
  its own engine, so under ASGITransport it would dial the real DSN; drive the
  pipeline directly, as `test_records_routes._extract_now` does.
- **The 30-second OTP resend cooldown still bites.** `e2e/reports.spec.ts` takes
  one token per phone through the API in `beforeAll` and waits out a 429; the
  doctor and dictation projects still need ~35s between runs.
- **`queue.today()` is the operating day** — UTC-based, and the scanner, board
  and console must all use it.
- Everything from the previous handoff still holds: no second prescription
  path, `unsaid` off without a transcript, `conclude` inside the S8 table,
  `patient_card` not narrowed to the assigned doctor, the spine never unmounts,
  and nothing captures an allergy.
