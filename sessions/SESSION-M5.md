# SESSION-M5 — The research assistant: reference for the doctor, authority for nobody

**Date:** 2026-08-08 · **Scope ref:** `sessions/SESSION-CLINICAL-INTEL-PLAN.md`
§4 + §6 (Session M5). Branch `main`. Baseline at start: backend **1,604** green.

M5 was the plan's natural next session rather than merely its next number: its
context is assembled from exactly what the first three modules produce — the
spine's signed-note diagnosis, M1's computed lab flags, M4's confirmed note tags
— and all three now exist. M3 (the PACS stub) stayed parked on its external gate
(§8.1), unchanged since M4.

## What this session was for

The fourth and last clinical-intelligence module, and the only one whose output
is prose. A doctor mid-consult types a question about the patient in front of
them, sees exactly what will be sent before it is sent, and gets an
evidence-summary answer that cannot reach the record.

## Acceptance criteria, restated and checked

- [x] Context assembly in code from the three modules that feed it, plus an age
      band — and the doctor can see and trim it *before* the first call.
- [x] `ResearchThread` / `ResearchTurn` + migration `9f2ab41c77d3`, per visit,
      per doctor, audited.
- [x] A `research_assist` prompt family pinning the register, refusing dosing
      and urgency, and grounding every answer in the local protocol.
- [x] The panel: context strip, multi-turn conversation, framing throughout.
- [x] Storage and audit of every exchange, with the context that left the box
      frozen onto the turn.
- [x] Cost guard per doctor per day.
- [x] Provider-down state: the panel says so, the composer closes, nothing queues.
- [x] **An explicit test that no clinical record can originate from a research
      answer**, structural rather than behavioural.
- [x] Gates: backend **1,660**, research E2E **7**, notes 5, dictation 8,
      conformance 48, voice-gw 25, build / `tsc` / `eslint` clean.

Not in scope and not done: retrieval and citations (plan §8.4, needs its own
design session), a doctor-facing history of their own past threads across
patients, and any admin analytics over what doctors ask.

## Decisions made

1. **The client can only subtract from the context, and this is the module.**
   Items have stable ids; the panel trims by id; the text is re-derived
   server-side on every turn. There is no field on any request model that
   carries context text, and an unknown id is a 400 rather than something
   ignored. The obvious alternative — let the browser post the context it wants
   sent — is one line shorter and gives away the whole PHI posture: `phi.
   assert_clean` can only vouch for a payload this repo *built*. A test posts a
   name and phone number both as an `include` entry and as invented body fields
   and asserts neither reaches the prompt.
2. **There is no parser for a research answer, and that is the safety property.**
   Every other LLM pathway parses into a contract, and each contract is a place
   a field could be added. This one sets `json_output=False` and stores prose.
   No schema means no field on a clinical record to reach, no formulary lookup
   to trigger, no printed sheet to appear on — a stronger guarantee than a schema
   with the dangerous fields left out. Pinned by a test that `.json()` does not
   appear in `assistant.py`.
3. **The tables have no status, no signature and no `applied` column.** A turn
   cannot be adopted into the record. The moment one can be marked accepted, a
   model's prose has become a clinical decision with a doctor's name attached,
   which is what plan decision 7 refuses. What a doctor takes from an answer
   they write themselves, on the Consult tab, in their own words.
4. **The daily guard is a count of turns, not a sum of rupees.** Metering is
   async and batched by design — it must never block a patient-facing call — so
   at the instant a doctor taps send, the cost of their previous turn may not be
   priced yet. A guard reading a number that lags by an unbounded interval lets
   a runaway client through and then reports accurately that the budget was fine
   when it checked. Turn count is knowable exactly, in the same transaction, and
   it bounds spend honestly because every turn has the same bounded shape.
5. **`context._diagnosis` re-queries rather than importing `app.doctor`.** The
   package imports no clinical writer, and `app.doctor` is one. What keeps the
   two readers honest is a test asserting they produce the same diagnosis for
   the same visit — the behaviour is pinned rather than the code shared.
6. **An unverified lab reading is labelled, not withheld.** Doc 21 §1.5's rule.
   Withholding would be worse: the doctor is looking at the same numbers on the
   Reports tab, and an answer that silently ignored them would be answering a
   different patient's question. It says so in the panel and in the text sent.
7. **It is a tab, and the argument is the inverse of M4's.** M4 is a dock
   because observations are captured *while browsing*. Reading an evidence
   summary is not done while reading something else — it is the thing being
   done, it wants the width, and a 52vh drawer would put a twelve-line answer in
   a four-line window. It also keeps one owner for the bottom of the screen.
8. **No sixth spine slot.** MRD2 took a fifth and wrote down the argument for
   refusing a sixth; a research thread has no count a doctor should act on
   without opening it, which is the test that slot has to pass.
9. **Suggestions are deterministic, built from the context.** A model proposing
   what to ask about a patient is a model steering a clinical enquiry — a much
   larger thing than answering the question a doctor chose to type.
10. **"AI Research" left the Coming-soon list; "NCCN Guidelines" stayed and was
    rewritten.** The MRD2 move. A guideline lookup against a retrieved source is
    a different feature with a different failure mode; letting the graduated tab
    absorb that line would tell a doctor their NCCN lookup is live.
11. **The outage message does not repeat the vendor's words.** "gemini http 503"
    on a clinical screen tells a doctor nothing they can act on. Two facts stay:
    it did not go, nothing is pending.

## The doc 04 §5 self-critique, and what it changed

Four things the screenshots showed that reading the code did not:

1. **The panel scrolled its own context strip behind the spine on open.** The
   auto-scroll to the newest answer fired on mount, so a doctor returning to a
   conversation landed below the one thing the panel exists to show first. It
   now scrolls only for a turn added in that session. The assertion is geometry:
   `toBeVisible` is true of an element under a sticky header, which is precisely
   how this passed while being wrong — the same trap M4 hit with its drawer.
2. **The note dock's mic sat on the Ask button.** Fixed first with bottom
   padding on `.rsx`, which separates them only at *maximum* scroll; the next
   screenshot caught the mic on the button at an ordinary scroll position, after
   the test had said it was fixed. The property that actually holds is
   horizontal — the dock is pinned to the right edge — so the button is kept
   left of it and the test checks three scroll positions.
3. **Context text ran underneath the mic**, hiding "the doctor mentioned grade
   1" behind another module's button. Every run of prose now stops at a 72ch
   measure, which is both better typography for the one surface with paragraphs
   on it and position-independent clearance.
4. **The outage state showed the doctor a vendor error string** (decision 11).

The screenshot helper itself was wrong twice, and both corrections are recorded
in it: `fullPage: true` renders `position: sticky` elements at their scroll
offset rather than where a doctor sees them — the first pass showed the spine
floating over the middle of the panel, a layout bug that did not exist — and an
unscrolled 720px viewport is all console and two lines of panel. Neither is the
frame to critique.

The deliberate aesthetic risk (one per surface): **unticked context is struck
through, not removed.** The obvious build hides what you turn off, which would
make a withheld line and a line this patient never had look identical — the same
failure the spine's "No red flags fired" exists to avoid, and the same one the
server answers with its `absent` list.

## Deviations from spec

- **The plan's mock-up shows "cycle 3 of AC-T"; the context does not.** Nothing
  in this record knows a cycle number, so it is absent rather than approximated
  from the nearest available thing. Four sources, exactly as §4.1 lists them.
- **The cost guard is not `app.providers.costguard`.** Decision 4. That guard is
  channel-keyed, schedule-driven and exists to degrade intake under load;
  `UsageEvent` has no doctor column to group by in the first place.
- **`RESEARCH_ENABLED=false` returns a 200 saying so rather than a 404.** An
  operator's decision the tab states; a vanished route looks like a broken build.

## Tests & evidence

- `make test-backend`: **1,660 passed** (1,604 → +56): 41 in `test_research.py`,
  15 in `test_research_routes.py`.
- `npx playwright test --project=research` → **7 passed** against a live stack
  (api on :8123 with `LLM_PROVIDER=fake`, web dev server on :3210,
  `scripts.seed_doctor_demo`).
- `--project=notes` **5**, `--project=dictation` **8**, `--project=conformance`
  **48** — unchanged.
- `make test-voicegw` **25 passed**; `make test-web` green.
- `npm run build` / `tsc --noEmit` / `eslint` clean. `/doctor` is 39.8 kB
  (35.7 → 39.8).
- Screenshots: `web/screenshots/m5/01…04`, self-critiqued above.

## Migration

**One, additive: `9f2ab41c77d3`** (`research_threads`, `research_turns`). No
existing row changes meaning and no backfill runs. It joins the five already
pending on Omen — `c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3`,
`02571a5c1871` — so there are now **six**, all applied locally only. `make
deploy` does not run migrations.

## Known gaps / stubs introduced

(Mirrored into STATE.md → Stubs & fakes.)

- **The answers have never been read by an oncologist.** Every test and every
  screenshot ran on `LLM_PROVIDER=fake`, whose reply is the string "ok". The
  plumbing is proven end to end; the *register* — whether the prompt's refusals
  hold, whether the trials it names exist — has not been checked against a real
  model even once. That is the first thing to do on a box with a real key, and
  it is a clinical review, not a QA pass.
- **v1 is the model's own knowledge, uncited and dated.** Plan §8.4. A doctor
  cannot follow an answer to a source, and the prompt is instructed to say when
  it is working from general knowledge rather than a nameable trial — which is a
  mitigation, not a citation.
- **The turn budget is per doctor per day and nothing else.** No per-department
  cap, no rupee ceiling, no admin surface to change it — `RESEARCH_DAILY_TURNS`
  is an env var and moving it needs a restart.
- **Nothing surfaces what doctors ask.** The plan calls that "itself the
  analytics"; the rows are there and audited, and no query reads them.
- **A thread cannot be deleted or amended**, the same shape as a signed
  dictation and a confirmed note. This system still has no amendment path.
- **The panel refetches the whole context on every open** and does not poll. A
  report scanned during the consult appears on the next open, not live.

## The two process failures worth recording

- **`prettier --write` produced a 71-file diff.** Prettier is not this repo's
  formatter: no config, no gate, not a dependency. The diff was reverted with
  `git checkout --` and the edits re-applied by hand. `web/.prettierignore` now
  ignores everything so the next occurrence is a no-op — a `.prettierrc` was
  considered and rejected, since at every print width tried (80/90/100/110)
  prettier still rewrites 54+ files, so a config would advertise a formatter
  that does not describe this code.
- **A backtick in a CSS comment took the doctor console down.** Every rule in
  `consoleStyles.ts` lives inside a template literal, and the comment explaining
  the dock-clearance fix contained `` `bottom: 24px` ``. The dev server 500'd on
  every `/doctor` request and it read like an auth regression for several
  minutes. The file now carries a note saying so, and the lesson is the ordinary
  one: re-run the gates after every edit, not after every few.

## Commits

(see `git log` on `main`, prefixed "S M5:")
