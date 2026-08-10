# HANDOFF — after SESSION-AYUR-2

**Repo state:** branch `main`, session close `036b6f3` (pushed to `origin/main`). `make test` green, exit 0 —
backend **1,899** (was 1,822), voice-gw 25, conformance **135** (was 115),
typecheck, lint, android.

**No migration this session.** The nine pending on Omen are unchanged:
`c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3`, `02571a5c1871`,
`9f2ab41c77d3`, `8ef31aa60c55`, `4ce8cb36a165`, `28e0ff23658b` — applied locally
only, and `make deploy` still does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**The AWS box was deployed to this commit on 2026-08-10** — `036b6f31`, up from
`3e5dd8f9`, 68 commits and six migrations, which `deploy/aws/deploy.sh` applies
itself. **docs/18 §0 is now the deploy ledger**: update that table at the end of
every AWS deploy, and read it before one, because the previous SHA it records is
`rollback.sh`'s argument. It also documents the trap that cost time this
time — `/opt/opd/current` is a **symlink** and the host carries two checkouts
(`repo` and `repo-new`); the live one was `repo-new` while docs 19/20 hard-coded
`repo`, so `git fetch` in the wrong directory reported `fatal: Invalid revision
range` and looked like a missing commit. Derive it with
`export SRC="$(readlink -f /opt/opd/current)"`. Omen is a different box and its
nine pending migrations are unchanged.

**Where the build stands.** AYUR-0 stored a system of medicine, AYUR-1 made the
hospital configurable, and AYUR-2 gave the ayurveda department **something to ask
and a way to be reached**. Five trees (37 nodes, 14 red flags, four languages),
and one new idea in the engine worth understanding before touching it:

- **A tree can now name the department a patient belongs in.** `Option.department`
  is the preference she states ("मैं आयुर्वेद इलाज के लिए आया/आई हूँ");
  `RedFlagSpec.route_to` is where a deterministic rule sends her. Both are
  resolved by **one** function, `Walk.destination()`, and the precedence is the
  safety rule: a fired flag's `route_to` wins, **any fired flag with no
  destination cancels a preference**, and only then does an answered option
  decide. A patient who asks for the ayurveda OPD and then reports chest pain is
  not moved on the strength of the asking.
- **Applied before the token, never after.** The token series is per department
  per day. `/kiosk/{sid}/confirm` re-homes the visit and then allocates;
  `confirmLocal` reads the same derivation and draws from the destination's
  leased block. That is why `destination` is now in the walker conformance
  fixture — a Python/TS disagreement here is a patient holding a number for a
  queue she is not in, during exactly the outage the offline walker exists for.
- **A closed department's question is removed, not disabled.**
  `app/trees/visibility.for_active` prunes it out of the canonical tree in
  `store.resolve_tree` and in `GET /kiosk/bundle`, so the kiosk never caches it
  and WhatsApp and telephony inherit the filter. `schema._validate_offers` is
  what makes that safe (two options, no option-keyed branch, read by no red
  flag), and the pruned tree is re-`parse`d, so a prune that orphaned a node
  fails a test rather than a kiosk.

**AYUR is seeded active.** The only thing holding it dark was that it had nothing
to ask. **On a box that has already been seeded this changes nothing** — since
AYUR-1 the loader never overwrites a department a console can edit — so the local
dev box needed the Facility tab (this session opened it through
`facility.update_department`, which is also the proof that AYUR-1's
`_assert_has_intake` guard now passes on its own). The clinical review has not
happened and is still a launch gate.

## Next session — SESSION-AYUR-3, the doctor console, capability-gated

Objective (doc 24 §8): wire the capabilities into the console bootstrap and hide
cycles / regimen lines / check-in / NCCN surfaces under ayurveda while proving the
**oncology console renders unchanged**; the ayurveda assessment panel and
pathya–apathya in the Rx composer and shared renderer; ayurveda formulary entries
and `validate_meds` scoping; the dictation-mapping and summary prompt packs; and
canned fake-LLM ayurveda replies.

**What AYUR-2 changes about how it starts:**

- **A patient can already arrive in AYUR two ways** — the chooser, and the offer
  inside the General Medicine or Pulmonology walk. So the doctor console will see
  ayurveda visits whose `Intake.tree_ref` is `general_medicine_routing@v3`. That
  is correct and deliberate: she answered those questions. Do not "fix" it by
  re-homing the tree.
- **The capability flags all exist already** and none of them are read by
  anything yet. AYUR-3 is the session that finally consumes
  `shows_cycles` / `shows_regimen_events` / `checkin_protocols` /
  `guideline_pack` / `formulary_scope` / `ayurveda_assessment` /
  `pathya_apathya` / `prompt_pack`. If you add a ninth, its sentence goes into
  `FLAG_LABELS` in `app/care_system.py` in the same edit — a test fails
  otherwise, on purpose — and `make care-system-fixtures` regenerates the TS side.
- **A card's styling reads the raw value, a component's behaviour reads a flag.**
  The kiosk chooser puts `care_system` on the DOM as `data-care-system` and lets
  the stylesheet decide; that is allowed and is the pattern for anything purely
  presentational. Anything that changes *what is shown* is a capability flag.
- Demo the whole thing on `LLM_PROVIDER=fake`, the MRD precedent.

First commands:

```
make dev && make migrate && make seed && make test
```

Then open Ayurveda in the console's Facility tab if this box was seeded before
today (the seed will report `kept (yours, not this file's): department=1`).

The three long-standing non-coding items are unchanged and still the most
valuable things nobody has done: **print a pass on the real printer** (doc 23
§11), **point M3 at the real `RAD-RENVA-PACS`**, and **have an oncologist read
the research assistant's answers** (asked in six consecutive handoffs now).
After those: **deploy the nine pending migrations to Omen** and give
`make deploy` a migration step.

## Watch out for

- **Two urgent red flags naming two departments break the tie by flag id.** In
  `ayurveda_respiratory` a patient with blood in the sputum *and* chest pain
  fires `ayr.tb_suspect` (PULM) and `ayr.chest_pain` (GENMED), and
  `ayr.chest_pain` sorts first. She is urgent and both flags are on the
  coordinator's strip either way, so nothing is lost clinically — but which queue
  gets the token is arbitrary between two urgents, and if that ever needs to be a
  clinical decision it belongs in the rule bank, not in a sort key.
- **An offering node has an authoring contract and `parse` enforces it.** Single,
  exactly two options, `next.default` only, and no red flag may read it. If you
  need a third option there, you need a different mechanism — not a relaxed
  check, because the whole point is that removing the node changes nothing else.
- **Adding a tree changes five counts.** `test_tree_bank.PILOT_BANK` and its file
  count, `test_offline`'s bundle tree count, `test_seed`'s department counts,
  `test_routing.EVAL_CASES`, and the routing eval set (a department with no
  labelled utterance fails `test_the_eval_set_covers_every_department`). All
  manifests; none of them are the test's meaning.
- **`bank.load_bank()` is `@cache`d and returns the *unpruned* trees.** Anything
  in the intake path must go through `store.resolve_tree` or
  `visibility.for_active`, or it will offer a closed department. The engine does
  this via `SessionState.open_departments`, which is `None` for sessions created
  before today and prunes nothing.
- **`web/e2e/people.spec.ts` is still red** and still predates all this:
  `people.spec.ts:54` clicks `nav button:has-text('People & roster')`, renamed to
  **"People and roster"** by `5be4c28` on 2026-07-27. One word, any session.
- Everything from the previous handoff still holds, in particular: **a seed file
  no longer wins over a row that already exists**; **`hospital.name` is almost
  never the right read — use `name_in(lang)`**; the two source tests that keep
  `CareSystem` out of every module but `app/care_system.py` and
  `web/app/_lib/careSystem.ts`; `published_trees` is not "can a patient be asked
  anything" (read `has_intake`); **do not run two live E2E projects in parallel
  against one database**; **never run `npm run build` while a dev server is up on
  3210**; re-run `seed_doctor_demo` before any doctor E2E; the three allergy
  states must never collapse into two; `consoleStyles.ts` is template literals and
  a backtick in a comment takes `/doctor` down with a 500;
  `OTP_RESEND_COOLDOWN_SECONDS=0` saves a wait on every E2E token; a new
  `Clinical` model must be registered in `tests/test_audit.py`; and `offline-demo`
  is still red and still predates everything.

## Decisions needed from the human

- **Now urgent — who is the BAMS practitioner?** Doc 24 §9 makes clinical
  sign-off a launch gate, and there is now content to sign off: five trees, 37
  questions and 14 red flags in four languages, all model-drafted. The TB rule in
  `ayurveda_respiratory.json` is the one to read first — it decides who gets sent
  to DOTS, and TB is notifiable. Until this happens, **Ayurveda must be closed in
  the console on any deployment a real patient can reach.**
- **Is "अलवर जिला कैंसर केंद्र" the right Hindi name for this hospital?** Asked
  last session, unanswered. It is on the Hindi kiosk's brand bar and on the
  patient's copy of every prescription, and an administrator can correct it in
  the console without a deploy.
- **Is "Ayurveda" the right department name?** It is now a card a patient taps,
  and it renders in English on a Hindi screen because a department has one name
  (see backlog). An administrator can rename it, but not per language.
- **Unchanged, now asked six times:** which thermal printer and when can someone
  stand at it; when can M3 be pointed at the real PACS and by whom; who reviews
  the research assistant's answers.
- **Unchanged from SESSION-ALLERGY:** does a coordinator need to record an allergy?

## Backlog additions

- **Department names are single-language.** `departments.name` is one string, so
  the kiosk chooser shows "General Medicine" and "Ayurveda" to a patient who
  chose हिंदी — visible in `web/screenshots/ayur2/01-chooser-ayurveda-card.png`.
  The hospital solved this in AYUR-1 with `name_i18n` + `name_in(lang)`; the
  same shape applies here, plus a column and a Facility-tab field. It is the most
  visible remaining flaw on the kiosk's first real screen.
- **An admin E2E for the facility tab, and a kiosk E2E for an ayurveda intake.**
  Both driven in a browser and screenshotted rather than committed, per the
  operator's instruction that E2E lands in the last ayurveda session. Whichever
  session closes the module inherits both.
- **`make lint` is red on 96 errors, all in `alembic/versions/`** — boilerplate
  from alembic's own revision template. A one-line decision about whether this
  repo lints generated migrations.
- **The downtime print sheets head in English** even on a Marathi or Telugu page
  (`routes/queue.py::_hospital_name`).
- **Marathi and Telugu have no hospital name of their own** and fall back to
  English. One entry in `TRANSLATABLE_LANGUAGES` (`app/facility.py`) plus the text.
- **`web/e2e/people.spec.ts:54` selector fix** — one word, any session.
- Everything already on the list: allergies on the boarding pass; a kiosk "I
  don't know" recording nothing; no coded substance vocabulary; `offline-demo`
  failing; desk-side re-print of a pass; linking an MRD `imaging_report` to its
  PACS study; `has_report` on a listed study; a local Orthanc mirror; retrieval
  and citations for the research assistant; an analytics surface over what
  doctors ask.
