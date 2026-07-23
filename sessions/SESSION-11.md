# SESSION-11 — Digital prescription

**Date:** 2026-07-23 · **Scope ref:** docs/06-BUILD-PLAN.md → S11 (doc 03 §8)

## Acceptance criteria checklist
- [x] **Signing generates the prescription.** Hooked inside `app.dictation.sign`,
      idempotent per dictation. doc 03 §7 always said this; S10 deliberately
      emitted nothing rather than write a half-shaped row.
- [x] **Rx sheet on hospital letterhead** — `render_clinical_copy`: masthead,
      patient strip, drug table, advice, follow-up, signature block with the
      signer's reg. no.
- [x] **Large-type pictogram patient copy** — `render_patient_copy`: one drug per
      band, morning/afternoon/night icons, patient's own language (en+hi).
- [x] **Print endpoint** — `GET /prescriptions/{id}/print?copy=clinical|patient`.
- [x] **WhatsApp / SMS delivery hooks via the provider layer** —
      `POST /prescriptions/{id}/deliver`, recording `delivered_via` per channel.
- [x] **Rx history on the patient file** — `GET /prescriptions/patients/{id}`.
- [~] **"PDF in <3s"** — the sheets render in microseconds, but they are
      **HTML the browser prints**, not a server-rendered PDF. Same deliberate
      call as S8's downtime sheets; see Deviations.
- [~] **"Pictogram copy passes low-literacy checklist"** — self-critiqued against
      doc 04 §5 and fixed three findings (below). **No low-literacy user has
      seen it**; a real checklist needs the S21 clinical/design review.

## What was built
- **`app/prescription.py`** — `parse_schedule` (the safety-critical piece),
  `RxLine`/`build_lines`, `generate` (called from `sign`), `for_dictation`,
  `for_visit`, `history`, `lines_of`, `record_delivery`.
- **`app/rx_sheets.py`** — `render_clinical_copy`, `render_patient_copy`,
  `sms_body`; en+hi string table, print stylesheet, pictogram rendering.
- **`app/routes/prescription.py`** — four routes, all `require_doctor`.
  No `POST /prescriptions`: the record is created by signing.
- **`app/dictation.py`** — `sign` now calls `prescription.generate` (local import;
  `app.prescription` reads this module's contract, so a top-level import cycles).
- **Web:** `_lib/prescription.ts` (typed client, no create verb),
  `_components/RxPanel.tsx` (appears under the note once signed),
  `consoleStyles.ts` (the `.rx-*` block).
- **`app/main.py`** — router registered.

## Decisions made
- **A dosing schedule is never inferred.** `parse_schedule` keeps two kinds of
  knowledge apart: *slots* ("1-0-1", "subah aur raat") and a bare *count* ("BD",
  "din mein do baar"). BD is conventionally morning-and-night in Indian practice
  and that convention is exactly what this refuses to encode — the pictograms are
  read by someone who cannot read the caption, so drawing a sun and a moon for a
  prescription that said only "twice a day" puts a time of day on the page that no
  clinician wrote. Count-known-slots-unknown renders as N tablet glyphs; anything
  unreadable ("SOS", "alternate days", "every 6 hours") returns `None` and prints
  the doctor's words with no icon.
- **A flagged drug prints flagged, on both copies.** `RxLine.flagged` deliberately
  does *not* mirror `meds_needing_attention`, which drops acknowledged drugs — the
  acknowledgement unlocked signing, it did not make the drug known, and the
  pharmacist never saw the console.
- **`lines_of` reads the stored snapshot** and does not re-run `parse_schedule`,
  so tightening the parser later cannot re-interpret a prescription already in a
  patient's hand.
- **A failed send is recorded, not raised.** The paper copy is the delivery that
  actually happened; the desk needs to see the message did not arrive.
- **The signature block names whoever signed the note**, not whoever pressed
  print — a covering colleague reprinting does not become the prescriber.
- **No `POST /prescriptions`.** If a create verb ever appears, "the signature is
  what prescribes" has been lost.
- **Print fetches with the bearer token and opens a blob** (matching S8's print
  tab) rather than putting a staff token in a query string, where it would land
  in every access log between the console and the box.

## Deviations from spec
- **doc 06 S11 says "Rx PDF"; this ships print-optimised HTML.** Identical call to
  S8 `app.print_sheets`, for the identical reason: the sheets must shape
  Devanagari, and a real HTML→PDF engine (WeasyPrint/pango) is a native
  dependency the image does not ship. The browser's print dialog makes the PDF.
  A server-side PDF is one decision for both sheet families (backlog, S19/S21).
- **No `Prescription` migration.** The model has existed since S2 and `meds` /
  `delivered_via` are JSONB, so the snapshot needed no schema change.
- **`pdf_url` stays null** — nothing is stored to object storage; the sheets are
  rendered on demand from the snapshot.

## Tests & evidence
- `make test`: **781 backend** (726 → 781), voice-gw 1, web typecheck + lint
  clean, 48 conformance. Green.
- New tests: `backend/tests/test_prescription.py` (55) — a mostly-negative
  schedule table, the flag-survives-acknowledgement set, generation off the
  signature (incl. idempotency and the audit row), delivery bookkeeping, history
  scoping, and the HTTP surface incl. a vendor outage recorded as `failed`.
- Screenshots (rendered from real sheet code, not the browser console):
  - `sessions/screenshots/s11/clinical.png` — letterhead copy. Reads as a
    document, not a form: the two flagged rows are the only colour on the page,
    each carrying its own reason, and every `as_spoken` line sits under the drug
    it produced. Nit accepted: dose/route/frequency columns are wide for a
    5-drug note and would tighten with 12.
  - `sessions/screenshots/s11/patient-hi.png` — Hindi patient copy. Icons lead,
    words support. Three findings from the doc 04 §5 pass were **fixed**: the
    Hindi duration read as a form label ("कितने दिन 5 days" → "5 days तक"), the
    morning and midday suns were near-identical shapes (morning now sits on a
    horizon line), and the patient strip had empty label cells. Remaining nit:
    "5 days" prints in the doctor's English because that is the dictated string —
    translating it would be inventing (S13).

## Known gaps / stubs introduced
(Mirrored into STATE.md → Stubs & fakes)
- **No live vendor has delivered a prescription** — WhatsApp/SMS go through the
  provider fakes, as everywhere else. Meta also needs a registered template for
  out-of-window sends; the template registry is S12.
- **The patient copy has never been read by a low-literacy patient.** The
  pictogram set is three glyphs chosen for printer-safety, self-critiqued only.
- **Durations and doses print in the doctor's own words**, so a Hindi sheet can
  carry "5 days" and "8 mg" in English. Translating dictated text would be
  inventing; per-value localisation is S13's question.
- **No amendment.** Signing is terminal, so a prescription is too — the same gap
  S10 logged, now with a printed artifact attached to it.
- **`pdf_url` is unused**; nothing is archived, so a reprint re-renders from the
  snapshot (which is why the snapshot exists).
- **mr/te fall back to English** on the patient copy (S13).
- **`make lint` was already failing** on 11 pre-existing unformatted files, none
  of them S11's. Not touched — it is not this session's scope, but it means
  `ruff format --check` is not currently a live gate.

## Commits
- `2a42819` — S 11: signing emits a prescription, and the schedule is read rather than guessed
- `fb8a38c` — S 11: the prescription on paper, and the two people who read it
- `3c0491a` — S 11: the prescription on the console, previewed exactly as it prints
