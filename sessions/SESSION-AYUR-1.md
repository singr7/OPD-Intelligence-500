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
- [x] Full gates green; no existing test body edited
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
- **`GET /kiosk/bundle` carries the hospital** — `{name, city}`, inside the ETag
  hash. This is the half of doc 24 §3.2 that turned out not to be true (below).
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

The stored name now wins **in every language**, with the constant as a fallback
used only before a bundle has ever been fetched. One untranslated proper noun is
the honest option: this platform was not given four versions of the hospital's
name and inventing them is worse than showing the real one. Whether the hospital
*should* have per-language names is a question for the operator, in HANDOFF.

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

**`make seed` still reverts a console edit, and this was left alone
deliberately.** `_upsert_hospital` and `_upsert_departments` overwrite `name`,
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

## Known gaps / stubs introduced

- **`make seed` reverts console edits to the hospital and to departments.** See
  Deviations. Not a stub introduced here — it is pre-existing and newly
  *reachable*, because before this session nothing else could write those rows.
- **AYUR is still dark, and now provably so.** The console's Open button for it
  is disabled and the server refuses the write; it opens by itself once AYUR-2
  publishes its trees. Nothing else about the department exists — no trees, no
  formulary entries, no prompt pack, no console sections.
- **`published_trees` counts only DB-published rows**, by design. The console
  translates it; any other consumer that wants "can a patient be asked
  anything?" must read `has_intake`.
- **The hospital has one name, in one language.** The kiosk shows it untranslated
  in all four. See HANDOFF → Decisions needed.
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
