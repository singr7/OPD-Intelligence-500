# SESSION-AYUR-1 — admin configurability

**Date:** 2026-08-09 · **Scope ref:** `docs/24-AYURVEDA-MODULE.md` §8 → SESSION-AYUR-1

## Acceptance criteria checklist

- [x] `PATCH /admin/hospital` (name, city, district, default_lang), audited
- [x] Department create/edit with a system-of-medicine selector and an active
      toggle, audited
- [x] Confirmation copy for a care-system change, stating what changes
- [x] Letterhead propagation verified — rename the hospital in a test, and the
      Rx print **and the intake pass** show the new name
- [x] Admin console surface for both
- [x] Full gates green
- [x] **Both HANDOFF decisions answered by the operator mid-session and built** —
      the seed no longer overwrites console edits, and the hospital has an English
      and a Hindi name. See the follow-up section.
- [ ] *"Admin E2E covering rename + department creation as ayurveda"* — **not
      committed as a spec**, per the operator's instruction that the AYUR
      sessions do session-scope tests and E2E lands in the last one. Both flows
      were driven in a real browser against a live stack and screenshotted; see
      Tests & evidence.

## What was built

- **`backend/app/facility.py`** — the two facts a hospital owns about itself
  that were previously only editable by editing `seeds/hospital.json` on the box
  and re-running the seed: what it is called, and which departments it runs.
  `identity` / `update_identity`, `list_departments` / `create_department` /
  `update_department`, and `care_system_impact`. Audited through
  `record_admin_action`; not versioned, the same stance `app.people` takes.
- **Five routes on the admin router** — `GET /admin/facility`,
  `PATCH /admin/hospital`, `POST /admin/departments`,
  `PATCH /admin/departments/{code}`, and
  `GET /admin/departments/{code}/care-system-impact`. `GET /admin/departments`
  is untouched.
- **`FLAG_LABELS` + `differences()` in `app/care_system.py`** — one plain
  sentence per capability flag, and the diff of two rows. The confirmation copy
  doc 24 §7 asks for is *derived* from these rather than authored.
- **`GET /kiosk/bundle` carries the hospital** — `{name, name_i18n, city}`, inside
  the ETag hash. This is the half of doc 24 §3.2 that turned out not to be true
  (below).
- **`web/app/(admin)/admin/_components/FacilityTab.tsx`** — a new console tab
  under a new **Facility** group: the letterhead card, the department table, the
  care-system change with its consequence list, and the create form.
- **`hospitalName()` in the kiosk's `i18n.ts`**, threaded through `useOffline` →
  `TopBar` and `TokenScreen` → `layoutPass`.

## Decisions made

**1. Doc 24 §3.2's "verify, don't assume" was right, and half of it was false.**
The doc says the letterhead "already reads stored hospital facts, so 'Ayurveda
Hospital' propagates for free — verify with the pass and Rx print tests, don't
assume." The **prescription** does read `Hospital.name`. The **kiosk brand bar
and the intake boarding pass did not**: they rendered `T.hospital` from
`_lib/i18n.ts`, a four-language constant compiled into the bundle — which had
*already* drifted, saying "Government Cancer Hospital, Alwar" while
`seeds/hospital.json` says "Alwar District Cancer Centre". An admin renaming the
facility would have changed the prescription and not the paper the patient is
handed at the door, and the two would have disagreed in the patient's hands.

The stored name now wins, with the compiled-in constant as a fallback used only
before a bundle has ever been fetched. It shipped first as one untranslated name
in every language, with "should the hospital have per-language names?" raised for
the operator — who answered *yes, English and Hindi*, which is the follow-up
section below.

**2. It rides on the bundle, not on `POST /kiosk/start`.** The brand bar is drawn
before any intake begins, and the pass must print during an outage — so the name
has to be in the kiosk's offline memory. It is in the ETag hash for the same
reason `care_system` was put there in AYUR-0: anything the kiosk *draws* from
the bundle must invalidate a cached one, or a rename never lands through the
next outage.

**3. A department cannot be opened onto an error, and this is now a check rather
than a comment.** `routes/kiosk.py` asserts `routed.tree is not None` after
routing and the chooser lists every active department, so activating a
department with no published tree and none on disk is a patient tapping a card
into a 500. `_assert_has_intake` refuses, naming the missing content. This is
the guard AYUR-0's handoff asked the next session to *remember*; the AYUR
department is now held dark by code and will open on its own the moment
SESSION-AYUR-2 authors `seeds/trees/ayurveda_*.json`. **The guard is
one-directional**: closing a department is never blocked, because closing only
ever reduces what patients are exposed to, and an operator taking a department
off the kiosk in a hurry must not be argued with.

**4. New departments are created closed** — same reason; one created a second ago
has no tree, so `active: true` at creation is refused by the same guard.

**5. The confirmation copy is derived, not written.** `care_system.differences()`
diffs the two capability rows and `FLAG_LABELS` supplies one sentence per flag,
so the console renders eight lines that are true by construction. A capability
added in AYUR-3 appears in the confirmation with nobody editing a paragraph, and
a test fails if a flag is ever added without its sentence. The console composes
no sentence about ayurveda at all, which is what keeps doc 24 §2's promise that
a third system of medicine is one enum value, one row and content.

**6. `GET /admin/departments` was left exactly as it was.** It is active-only
because it feeds the create-a-doctor picker, and a console must not be able to
hire somebody into a department no patient can reach. `GET /admin/facility` is
the editor's read and it is the one that sees the closed departments — that is
what an editor is *for*. Two reads, two jobs, no flag on one endpoint.

**7. RegistryTab was the wrong home.** Doc 24 §7 offered it "or a sibling if
RegistryTab is the wrong home — executor judges on reading it". It is read-only
WhatsApp template coverage and voice packs; nothing about it is the facility.
New tab, new **Facility** group, placed between Operations ("can a patient reach
us at all") and Workforce ("is there anybody to send them to").

**8. The letterhead card is the tab's one deliberate risk (doc 04 §5).** The
hospital's name is not a form field labelled "Name" — it is drawn as the top of
the page it prints on, centred and ruled and captioned *as it prints*, and it
previews what is being typed. Renaming this facility renames a prescription and
a boarding pass; that is the consequence the operator should be looking at while
they type it. Everything else on the tab is a quiet table.

## Deviations from spec

**No admin E2E spec was committed.** Doc 24 §8 lists one as AYUR-1's evidence.
The operator instructed at the start of this session that per-session E2E is not
the bar for the AYUR sessions and that E2E lands in the last one. Both flows
were driven in a real browser against a live stack, asserted, and screenshotted;
the spec was then deleted rather than committed. AYUR-4 (or whichever session
closes the module) inherits it.

**~~`make seed` still reverts a console edit~~ — the operator decided this
mid-session and it is built; see the follow-up section below.** What was
originally written here: `_upsert_hospital` and `_upsert_departments` overwrite `name`,
`icon`, `active` and `care_system` from `seeds/hospital.json` on every run, so
renaming the hospital in the console and then running `make seed` puts the
seeded name back. Fixing it means changing
`test_seed_updates_in_place_when_reference_data_changes`, which exists precisely
to assert the current behaviour — and doc 24 §8 forbids editing an existing
test's body. Doc 24 §8 asked AYUR-1 for a *decision* on this (it is a backlog
item from AYUR-0), and the decision is: **not in this session, and it is a
question for the human**, because it is a policy call about what a seed file is
for. `make deploy` does not run the seed, so it only bites an operator running
it by hand. Written up in `app/facility.py`'s docstring, in HANDOFF, and in
STATE → Stubs & fakes.

**One incidental commit.** `ruff format` over `app` and `tests` picked up four
files the AYUR-0 handoff had already recorded as making `make lint` red. They
are whitespace only and landed in their own commit (`192dc80`) rather than
inside a feature diff.

## Tests & evidence

- **`make test` green** — backend **1,803** (was 1,771), voice-gw 25,
  conformance **115**, typecheck, lint, android. **No existing test body was
  edited**; the delta is exactly the 32 new ones.
- **New tests:** `backend/tests/test_facility.py` (32), in five blocks — the
  rename reaching the paper, the activation guard, the system-of-medicine
  change, the audit trail, and the two department reads staying apart.
- **The headline AC is asserted against the sheet a pharmacy reads**, not against
  the PATCH's own response: the prescription is re-fetched after the rename and
  the old name must be *absent* from it, because a letterhead carrying both is
  one carrying a stale name somewhere.
- **No migration.** Nothing in this session changes the schema; the pending-on-
  Omen list is still the eight from AYUR-0.
- **Screenshots** — `web/screenshots/ayur1/`, driven against a local api on 8123
  and a dev server on 3210:
  - `01-facility.png` — the letterhead reads as a printed page rather than a
    form, which is what it is for. **This shot caught a real bug**: the table
    said "Intake trees: 0" beside "OPEN" for nine of ten departments, because it
    counted *published* rows while the guard uses `resolve_tree`, which falls
    through to the seed bank. The surface described an impossible state and
    contradicted its own caption. Fixed to say where the questions come from —
    "N published", "from the tree bank", "none yet" — and "none yet" is the one
    row whose Open button is disabled, which is AYUR and only AYUR.
  - `02-letterhead-editing.png` — the letterhead previews the new name as it is
    typed. Reads well; the caption *as it prints* is doing the work.
  - `03-care-system-change.png` — eight derived consequence lines with +/−/→
    marks. The one thing I would still question: the amber notice is the same
    treatment the roster import's warning uses, so a reader who has seen that
    one may under-read this. Left as is — it is a confirmation, not an alarm,
    and the button underneath carries the whole sentence.
  - `04-new-department.png` — the create form, whose primary button says "Create
    it closed" rather than "Create", so the outcome is on the control.
- **Verified in a browser and not committed as a spec** (per the operator's
  instruction): renaming the hospital through the console changes the **kiosk
  brand bar** on the next load, from "Alwar District Cancer Centre" to "Alwar
  Ayurveda Hospital". That is the claim doc 24 §3.2 told us to check rather than
  assume, and it is the one that was false before this session.

## Follow-up in the same session: the two decisions, answered

The operator answered both HANDOFF questions before the session was handed on,
so they are built here rather than deferred.

**1. `make seed` no longer overwrites what an administrator set up.** The chosen
rule: *`seeds/*.json` describes a box nobody has set up yet.* For the rows a
console can edit — hospital, departments, staff users, doctors, clinic templates
— the loader creates what is missing and never overwrites what it finds. Adding a
department or a doctor to a file and re-running is still how new reference data
arrives. Two things keep it from becoming a silent no-op: a fourth report bucket,
**`kept`**, counts and logs a row left alone *because it differs*, so the run says
"I noticed and stood down"; and the file is still **validated on every run**,
written or not, so a typo in an existing department's `care_system` still raises
rather than going unseen for exactly the rows old enough to have been edited.
Patients are exempt (generated demo data, no console), as are the price book, the
tree bank and the protocol bank (versioned or append-only, with editors of their
own).

`test_seed_updates_in_place_when_reference_data_changes` asserted the behaviour
being reversed, so it is **restated, not deleted**, with the reason in its
docstring — the operator's decision is what authorises it, and this is the second
existing test body doc 24's "no test-body edits" rule has met a genuine exception
for. Two tests were added for the other half: new entries in the file are still
created, and a misspelt value in a row the loader will *not* write is still
refused.

**2. The hospital has an English name and a Hindi one.** `Hospital.name_i18n`
(JSONB, migration `28e0ff23658b`, additive, **no backfill**) with `name` as
English and as the fallback, read through one derivation, `name_in()`.

The sweep is the substance: **sixteen call sites** read `hospital.name` while
holding a language — the staff invite SMS, the D-1 campaign (two), check-in and
cycle messages, the patient app's missed-dose SMS, the care file (two),
appointment notifications on four paths, the WhatsApp bot (two), the prescription
SMS and its WhatsApp template, and the letterhead. All now read
`name_in(<that language>)`.

Two deliberately do not. The **clinical copy** of a prescription is the file's and
the pharmacy's, gets photocopied into a chart, and stays in the name everyone can
read — `name_in(None)`, which is English; the patient copy follows the patient.
`routes/queue.py`'s **downtime print sheets** keep English too, because they
render every language on one page under a single header, and per-language headers
there is a `print_sheets` change this session did not need.

*Hindi only*, per the operator: `TRANSLATABLE_LANGUAGES = (Lang.HI,)` is the whole
of it, and widening it is one tuple entry plus the text — the column is JSONB, the
console renders whatever the tuple lists, and the kiosk falls back per language on
its own. A test pins that property. Marathi and Telugu fall back to English rather
than carry a model-drafted guess at a facility's own name, which is the first line
of the kiosk and the top band of a document the patient carries out of the
building; that is a stricter stance than this repo takes for tree text, and it is
deliberate.

`parse_name_i18n` refuses three things that would each store fine and then fail
*silently* at render time: a language not in the tuple, an `en` key (English is
`name`; two places to write it is one place for them to disagree), and text in the
wrong script — pasting the English name into the Hindi field is the likeliest
mistake here, and the letterhead would still render, just in the wrong language to
the one person who needed otherwise. Blank entries are dropped so that absent and
empty mean the same thing.

The console's letterhead card gained an **English / हिंदी toggle under the
"as it prints" caption**: the way to check a translation is to see the page in it,
not to re-read the field you just typed into.

Evidence: `make test` green — backend **1,822** (from 1,803), conformance 115,
voice-gw 25, typecheck, lint, android. Screenshots `05-hospital-name-hindi.png`,
`06-kiosk-hindi-brand.png` (the Hindi kiosk brand bar; switching to मराठी falls
back to the English name), `07-wrong-script-refused.png`. All three flows driven
in a real browser and then deleted rather than committed as specs, per the
operator's E2E instruction.

## Known gaps / stubs introduced

- **~~`make seed` reverts console edits~~ — fixed in this session**, see the
  follow-up above.
- **Marathi and Telugu have no hospital name of their own**, and fall back to
  English. Deliberate, and the operator's call; adding them is one entry in
  `TRANSLATABLE_LANGUAGES` and the text.
- **The downtime intake sheets and the token-block sheet head in English**, even
  when printed for a Marathi or Telugu page. Per-language headers there need a
  `print_sheets` signature change; backlog.
- **AYUR is still dark, and now provably so.** The console's Open button for it
  is disabled and the server refuses the write; it opens by itself once AYUR-2
  publishes its trees. Nothing else about the department exists — no trees, no
  formulary entries, no prompt pack, no console sections.
- **`published_trees` counts only DB-published rows**, by design. The console
  translates it; any other consumer that wants "can a patient be asked
  anything?" must read `has_intake`.
- **~~The hospital has one name, in one language~~ — fixed in this session**, see
  the follow-up above.
- **`make lint` is still red** — 96 errors, *all* of them in `alembic/versions/`,
  all of them boilerplate from alembic's own revision template (`from typing
  import Sequence, Union`, `Union[...]` annotations, a long `sa.Enum` line). The
  `ruff format --check` half that the AYUR-0 handoff recorded is now fixed. The
  remaining half is a one-line `exclude` decision about whether this repo lints
  generated migrations, which is policy rather than a bug.

## Commits

- `192dc80` — S AYUR-1: ruff format the four files make lint was red on
- `1343bd6` — S AYUR-1: a hospital can be renamed and a department can change system
- `1332f4f` — S AYUR-1: the hospital's real name reaches the pass, not just the Rx
- `7dd4a0b` — S AYUR-1: the console gets a facility tab, and it looks like a letterhead
- `3ea5711` — S AYUR-1: the intake column says where the questions come from
- `d9b012e` — S AYUR-1: session close — a hospital its administrator can rename
- `198dd30` — S AYUR-1: the seed stops overwriting what an administrator set up
- `92c3529` — S AYUR-1: the hospital has a Hindi name, and it reaches the patient
