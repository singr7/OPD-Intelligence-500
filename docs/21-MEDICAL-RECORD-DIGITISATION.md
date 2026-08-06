# 21 — Medical Record Digitisation (MRD)

The module built in SESSION-MRD1 (capture and pipeline) and SESSION-MRD2 (the
doctor's Reports tab and the desk's retry surface). Deploying it is doc 22.
Planned in
`sessions/SESSION-CLINICAL-INTEL-PLAN.md` §1, which also holds the three modules
not yet built (PACS viewing, ambient notes, research assistant).

**The intent, in one sentence:** the doctor opens the consult already knowing
what the patient's papers say.

---

## 1. The flow

```
Intake completes ──► coordinator opens /scan on their phone
                        ▼
                 picks the patient (today's queue, or token/phone/UHC search)
                        ▼
                 photographs the pages; each uploads as it is taken
                        ▼
                 taps Done ──► document is `captured`, extraction is nudged
                        ▼
                 vision LLM: page images → printed values, verbatim (§1.4)
                        ▼
                 deterministic pass: ranges compared, flags computed in Python
                        ▼
                 text LLM: ≤8 lines over the *flagged structure*, not the pages
                        ▼
                 doctor's console: values, flags, summary, original pages
```

### 1.1 States, and what each one shows a doctor

`DocumentStatus` (`app/models/enums.py`). There is deliberately no state meaning
"we tried and will say nothing about it".

| status | what the doctor sees |
|---|---|
| `capturing` | nothing — pages are still arriving; half a report is not a report |
| `captured` | the pages, with "waiting to be read" |
| `extracting` | the pages, with "being read" |
| `extracted` | values, flags and pages — the summary may still be coming |
| `summarized` | all of it. The terminal happy state |
| `extraction_failed` | the pages, and a sentence saying why the machine could not read them |

A failed reading degrades the module to a photo viewer. It never hides a
document: that is exactly when the doctor most needs to know the paper exists.

### 1.2 Capture surface (`/scan`)

Phone-first, installable (`/scan.webmanifest`), behind the same staff token as
`/coordinator` — the same person on the same shift. Three screens: pick,
capture, done. The picker searches by **token, phone or UHC ID and never by
name**: a name search on a staff phone at a public desk turns one shoulder-surfed
screen into a browsable oncology register.

Pages are downscaled to 2000px / JPEG 0.8 on the phone before upload, and posted
one at a time so a coordinator called away keeps what they got. A failed upload
is visible, retryable, and blocks Done.

**Not built:** a service-worker queue that survives a reload. Failed pages retry
within the session only, and the screen says so.

### 1.3 Storage

Page bytes go to an `ObjectStore` (`app/providers/objectstore.py`), not
Postgres: a few hundred kilobytes per page at ~500 patients/day would double
every backup for bytes that never take part in a query. The filesystem impl is a
directory on the box — a Compose volume, no new service. There is no S3 impl.

Deliberately **not** a `Provider`: it wraps no vendor, so it meters and prices
nothing, and a row in `usage_events` for a local disk write would reconcile to
nothing on the S18 dashboard.

> **Operator consequence.** The backup job must include `OBJECT_STORE_DIR`.
> Postgres alone is no longer a complete restore — the extractions come back
> pointing at pages that are gone. The page route answers `410` for exactly this
> case, so it is visible rather than a broken image.

Page bytes are streamed by the backend under the auth guard, never handed out as
a signed URL: a link to a patient's lab report that keeps working after the
session that minted it — in a browser history, a chat, a screenshot — is a
disclosure with a long tail.

### 1.4 The extraction contract, and the line the model may not cross

The model reports what is **printed**: test name, value, unit, the reference
range beside it *if the lab printed one*, the page, and its own confidence. It
transcribes or omits — a value it cannot read is left out and the region named
in `illegible_regions`. This is `dictation`'s "never silently corrects or invents
a drug" applied to a lab report.

**The contract has no flag field**, in the schema or the prompt. Whether a value
is abnormal is computed in `app/mrd/ranges.py`, in Python, on `Decimal`. A reply
that volunteers `"flag": "critical_low"` is ignored, and a test says so. This is
CODEBASE_MEMORY's invariant — *a model may interpret or summarize; it may not
decide clinical urgency* — applied to numbers.

Two sources of range, in order:

1. **The range printed on the report.** Preferred always, compared with *no unit
   conversion at all*: the value and its range came off the same page in the
   same units, so normalising could only introduce error, and the lab calibrated
   that range to its own analyser. It also makes any of a lab menu's hundreds of
   tests flaggable, where our table has eighteen.
2. **`seeds/lab_reference_ranges.json`** — adult, sex-aware, and marked
   `review_pending`. Every flag carries `ref_source`, so a UI can show a
   table-derived flag as the weaker signal it is until an oncologist signs the
   file off.

Neither available, or a unit we cannot convert → `UNKNOWN`, shown plainly. A
platelet count of 150 is normal in 10³/µL and catastrophic in /µL, and the page
does not say which if we guess. Name matching is exact-or-alias, never fuzzy:
the formulary can fuzzy-match because it *suggests* to a doctor who then
chooses, and nobody chooses here.

The summariser is shown the **flagged structure, not the images**. Beyond
costing a fraction as much, it makes the prose provably about the same numbers
the doctor's table shows; a second reading of the pages could disagree with the
first and nothing would say which one the summary described.

### 1.5 Doctor surface

Built in **M2**. The console gained a fifth tab, **Reports**, and the context
spine a fifth line.

The rule that surface keeps: an unverified machine reading of a lab report is a
**draft**, and every screen showing one says so until a doctor taps *Mark
reviewed*. Re-extraction clears a previous verification — a re-run is a new
reading, and carrying the old signature onto it would put a doctor's name on
numbers they never saw.

Three things, in this order, and nothing else competes with them:

1. **The summary, stamped.** Dashed border and an `Unverified` chip until it is
   reviewed; afterwards, who checked it and when.
2. **The values, weakest signal marked.** Out-of-range rows first, the rest
   behind *Show n within range*. Every row states which range decided it —
   `printed on report` or `our range` — because a flag from
   `seeds/lab_reference_ranges.json` is the weaker claim until an oncologist
   signs that file off (§8.1), and burying that in a footnote would make the two
   look alike. `UNKNOWN` is shown plainly and never folded into "normal".
3. **The original pages**, and every value's page number is a button that opens
   that photograph. The original is one tap from any number, which is the whole
   basis for trusting the table above it.

**The spine's fifth line.** The spine's own rule is that a fifth permanent slot
means it has stopped being readable, and this is the exception, argued: the
module's stated intent is that the doctor knows before the patient is in the
room, which a badge on a tab nobody has opened does not achieve. So it is one
line that never wraps, a link into the tab rather than content in its own right,
and amber at its loudest — red on that console stays reserved for the
deterministic red-flag lane. A sixth slot should still be refused.

**Page bytes and the browser.** `<img src>` cannot fetch them: the route is
guarded and the staff token is in `localStorage`, so the only thing that would
make the tag work is a signed URL, which §1.3 refuses. `PageViewer` fetches the
bytes with the bearer token and revokes the object URL on unmount — a console
left open on a ward machine all morning must not accumulate every page of every
patient it has shown. A 410 is rendered as its own state, not a broken image,
because it means Postgres was restored without the pages directory and an
operator needs to hear about it.

### 1.6 The desk finds out what did not read

`GET /records/scan/failures` and a *Could not be read* section at the foot of
`/scan`. M1 shipped `retry` with nothing calling it: a failed document was
honest on the doctor's screen, but the person who can re-photograph it was never
told.

It is deliberately **not** a `DocumentOut` — no `extraction` field, and it must
never grow one. A coordinator is not `require_clinical`; being told "these pages
did not read" must not become a way to browse the reading. Bounded to a week,
because a list that only grows stops being read.

---

## 5. Cross-cutting pieces this module added

- **Vision in the provider contract** — `ImagePart` on `LLMRequest`, mapped to
  Gemini `inline_data` and OpenAI `image_url` parts. Capability is declared per
  provider: a text-only vendor raises `UnsupportedCapability` *before* it is
  dialled rather than having the images stripped and answering from nothing. It
  subclasses `ProviderUnavailable`, so a chain walks past a text-only primary to
  a vision model.
- **`app/phi.py`** — one implementation of what may leave the box, shared with
  the research assistant when it is built. It *names* the fields that may go
  (age band, sex, diagnosis) rather than filtering out the dangerous ones: under
  a denylist, a column added to `Patient` next year reaches a vendor by default.
- **`UsagePurpose.DOCUMENT`** — its own purpose, because a document is priced per
  page of image and an intake summary is not.

---

## 8. Debt and external gates

1. **`seeds/lab_reference_ranges.json` needs oncologist review.** It ships
   `status: review_pending`, and that flag is what the UI keys off to show
   table-derived flags as the weaker signal. Eighteen tests, adult only, no
   paediatric or pregnancy ranges.
2. **No S3 object store.** The seam exists; the cloud shape needs the impl.
3. **The backup job does not yet include the pages directory.** Recorded in
   STATE → Stubs & fakes, in `.env.example` beside the setting, and in doc 22
   §2 with a sketch of the tar step. M2 gave the directory a real volume on both
   compose files — before that it had none at all, so pages did not survive a
   container recreate and the worker could not see them — but backing it up is
   still unstarted, and the restore side has never been exercised.
4. **Extracted values feed nothing but display.** They do not reach prescription
   validation, check-in grading, or trends across visits. That is future work
   behind its own clinical review.
5. **Handwritten outside prescriptions will extract poorly.** `illegible_regions`
   plus original-page viewing is the pilot answer; measure before promising more.
6. **No offline capture queue** (§1.2).
7. **Storage grows without a lifecycle policy.** Low single-digit GB/week at
   pilot volume — fine for the box, not for year one.
8. **The document kind cannot be changed after the first photo.** It is fixed
   server-side at creation and only steers the extraction hint, so a mislabel
   costs nothing clinically; correcting it means cancelling and re-scanning.
