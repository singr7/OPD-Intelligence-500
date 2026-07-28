# SESSION-UX6 — kiosk intake and doctor console rejig

Branch: `ui-ux-rejig` (from `aws-ubuntu-live-prep`), merged to `main`.
Date: 2026-07-28.

## Why

The demo surfaces were correct but not convincing. Nine specific complaints, all
about the same underlying problem: the kiosk asked the patient for things it then
forgot, showed a live rail that vanished exactly where it was most needed, put a
microphone on every screen, and read English option labels back to Hindi
speakers; and the doctor console spread one encounter across three places so the
doctor could not tell whether a consult was finished.

## What changed

### 1. Registration is asked once, and typed (kiosk)

The single spoken `name` screen became a `details` screen: **name, age, gender,
phone**. `Patient.age/sex/phone` are now populated by the kiosk (online) and by
the offline replay (`app/offline.py`), normalised once in `app/patient_names.py`
so the two paths cannot land different rows. Only the name is required; the rest
are optional and a typo is dropped rather than refused — an intake must never
fail on a demographic.

Deliberately **no microphone on this screen**. A misheard name is a different
patient, and it now travels to the token slip, the queue, the board, the doctor
console and the prescription.

### 2. The rail is on every screen

`SummaryRail` now renders through `Stage` on caregiver → details → complaint →
chooser → question → **read-back**, which is where it used to disappear and where
the patient is being asked to confirm. It records *every* answer, not only the
ones the tree tagged with a `summary_role`, newest first, capped at five with an
honest "+n more".

### 3. The microphone appears where speech is the better answer

`get_next_node` now returns `remaining` (questions left on the tree's default
path) and `voice_input` (a free-text node always; a tap node only in the closing
pair, `VOICE_TAIL_QUESTIONS = 2`). The offline walker computes the same two
values in `offline/local.ts` — they must stay twins.

Progress became a **countdown** ("2 questions left" / "last question") rather
than "3 of 8": the trees branch, so a total is a promise the walk cannot keep.

### 4. Options are read aloud

`spokenFor()` speaks the question followed by its choices. A patient who cannot
read the screen has not been offered a choice they never heard.

### 5. The summary carries the whole intake

- `summarize@v2` adds `final_words` as its own prompt variable and requires every
  answered question to be reflected. The closing free-text answer is the line a
  summariser under a word budget drops first and the one the doctor most wants.
- `TemplateSummarizer` (the V3 / offline / no-vendor path) now builds the
  read-back from every answered question in the patient's language plus their
  closing words, instead of one line naming the complaint.
- **Bug fixed:** `_value_text` hardcoded English for single-select answers. The
  doctor's summary never noticed (it asks for English); the patient's spoken
  read-back did — it told a Hindi speaker "You told me: My periods are irregular".

### 6. Trees branch instead of asking absurd questions

No new trees, no routing change. `gynae_routing@v2` splits on the first answer:
irregular/missed periods get a cycle question, discharge/itching/burning get an
RTI-shaped question, pain gets a severity scale — and only actual bleeding gets
"are you soaking a pad in an hour". `general_medicine_routing@v2` asks for a
temperature only on a fever complaint.

`test_the_routing_trees_stay_thin` now measures the **longest walked path**
rather than the node count: branching adds nodes while keeping each patient's
walk short, and counting nodes would punish exactly the thing that makes the
questions relevant.

### 7. The queue names people

`EntryView.patient_name`, `DepartmentBoard.doctor_name` / `now_serving_name`.
The board shows the department, the doctors on duty (all of them, joined — the
queue is per-department, so naming one of two would be a guess), the serving
token with its patient, and named next-tokens. The coordinator list leads with
the name. **Note:** this puts patient names on a public screen. It is what a
government OPD board already does, and no clinical reason ever appears there —
but it is a deliberate change to what the board exposes.

### 8. One encounter, one next step (doctor console)

New `EncounterBar` above the card: states the encounter in words ("In
consultation · Token 12 · Kamla Devi"), and carries exactly one filled button —
the thing to do next. `Call next patient` is primary only when the room is free
and quiet otherwise, because a doctor tapping it by reflex mid-consult has just
skipped the person in front of them. "Done" became "Complete consult"; "Dictate
note" became "Write consult note" / "View consult note". The state chip left the
card: two half-answers to "where am I" is what made it ambiguous.

The left rail is unchanged in purpose and keeps names, ages, complaints and red
flags.

## Verification

- `backend`: 1250 passed. The 12 failures are the pre-existing date-dependent
  `test_roster` / `test_people` / `test_config` / `test_checkin_delivery` set,
  confirmed identical on a stashed tree before any change.
- `make test-web`: typecheck, lint and 48 walker-conformance traces green.
  Fixtures regenerated for the two tree revisions.
- Playwright against the live stack: `kiosk` 3/3, `doctor` 7/7 (was 5/7 — two
  tests asserted behaviour that no longer exists: a CSS-module-hashed `.hint`
  selector that could never match, and an "S10 not built yet" toast), new
  `ux-smoke` 2/2 (portrait kiosk 1080×1920 and laptop 1366×768: no horizontal
  scroll, primary action reachable).
- Live API smoke: menstrual-irregularity and RTI intakes take their own branches;
  the mic is offered only on the last two questions; demographics land on the
  patient row; the read-back is fully Hindi.

## Known gaps / not done

- The coordinator and admin surfaces were explicitly out of scope. The
  coordinator's sign-in Playwright test still fails on the same pre-existing
  `.hint` selector; the console itself is fine and now shows names.
- `mr`/`te` strings for the new screens and the new gynae options are
  model-drafted and still need native review (same standing caveat as the rest of
  the bank).
- The gynae and general-medicine tree revisions need oncologist/clinician sign-off
  before a real patient sees them. They are structurally valid and versioned; that
  is not the same as reviewed.
