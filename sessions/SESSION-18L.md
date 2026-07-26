# SESSION-18L — Admin console remainder (S18-late)

**Date:** 2026-07-26 · **Scope ref:** docs/06-BUILD-PLAN.md → S18 (the S18-late half; the
analytics dashboard, tree publish→live, price book and cost guard shipped early as
`sessions/SESSION-18E.md`)

## Acceptance criteria checklist
- [x] **A non-technical user edits a tree option, publishes, and sees it live on the kiosk
      with no deploy.** S18E made this true for someone willing to edit JSON; the visual
      editor makes it true. Proven end to end against a live stack by
      `web/e2e/admin.spec.ts` → *"editing an option, publishing, and the kiosk serves it"*:
      the option text is typed in the console, published, and then read back from the
      **intake path itself** (`POST /kiosk/start`), not from the console's own state.
- [x] **What-if recompute matches a hand calculation on fixture data.** The tier-mix half of
      doc 03 §11 now exists: `test_tier_mix_matches_a_hand_calculation` (3 phone intakes at
      ₹4.00 median → ₹1.50 median = −₹7.50, checked by hand). The price-book half was S18E's.
- [x] **Protocol-template editor** — the bank is a table (`protocol_banks`), versioned,
      draft/publish/rollback, validated by `protocols.parse` on every save, with the panel
      rewritten from read-only to editable.
- [x] Every editor write audited (`record_admin_action`) and validated; `parse()` remains the
      only constructor for both trees and banks.
- [x] `make test` green: backend **1082** (was 1071), voice-gw 22, web typecheck + lint + 48
      conformance, Android 6. `make lang-qa` clean across [en, hi, mr, te].
- [ ] **Slot-template editor** — not built. Still the one honest deferred placeholder.
- [ ] **Editable message-template registry** — not built (still code-defined, read-only).
- [ ] **Voice-pack upload** — not built; the pack storage format is still S7's and unbuilt,
      so the panel stays a coverage view (an uploader would invent a layout).
- [ ] **Node-level abandonment report** — not built; still needs per-node answer timestamps
      the intake path does not emit.

## What was built

- **`protocol_banks` + `app/checkins/store.py`** — the check-in protocol bank moves from a
  seed file read once at boot to a versioned table, the way S4's trees moved.
  `resolve_bank(session)` prefers the newest published row that parses and falls back to
  `seeds/protocols.json`; `published_bank` skips a row that no longer validates rather than
  failing. Wired into every entry point: `plan._draft` (resolved once and threaded through
  protocol choice, schedule and personalisation, and recorded as
  `personalisation.bank_version`), `plan.approve`, and the plan view in `routes/checkins.py`.
- **`Checkin.grading_rules`** — the rules a check-in will be graded by, frozen onto the row
  beside the questions `asked` already froze. Forced by the bank becoming editable: a grade is
  recomputed on every answer, so live rules would let an afternoon's publish re-decide
  Tuesday's answers. `grading.grade` prefers the snapshot, reads the bank only for rows
  written before the column, and grades **amber by hand** if a snapshot no longer validates.
- **`admin.list_protocol_banks` / `save_protocol_bank_draft` / `publish_protocol_bank`** +
  `GET/POST /admin/protocol-banks*`. The version number is assigned server-side and written
  into the document, so the row and `parse()` can never disagree about which bank this is.
- **`web/.../TreeEditor.tsx`** — the visual editor. The tree drawn as a spine in ask order,
  branches indented under the option that leads to them, red-flag stations stamped and
  tinted; question text, option labels and red-flag label/instruction/severity editable in
  each of the four languages; a try-it panel that dry-walks the *edited* JSON through the
  real walker (`POST /admin/trees/test-run`); save → publish as two deliberate taps.
- **`web/.../ProtocolsTab.tsx`** (replaces `ComingSoonTab`) — the live bank, what escalates,
  the version rail with publish/rollback, and a document editor that shows the validator's
  own refusal verbatim.
- **`analytics.tier_mix` + `GET /admin/analytics/tier-mix`** + the Cost tab panel.
- **`app/auth/otp.py`** — the baseline fix (below).

## Decisions made

- **The protocol bank is versioned as one document, not one row per protocol.**
  `protocols.parse` cross-checks the whole bank — no orphaned question set, no tied
  precedence, no rung naming a set that does not exist. A protocol-at-a-time editor would let
  a half-edit pass row-level validation and fail those checks later, on a box, at the moment a
  doctor signs a note.
- **Seeded as draft.** Same argument as the trees, and stronger: this bank rings a doctor's
  phone at thresholds no oncologist has signed off. Until a human publishes, `resolve_bank`
  falls through to the identical file, so the table changes nothing by existing.
- **The tree editor edits words, not shape.** Adding, deleting or rewiring a question stays in
  the seed file and a pull request, where the validator's unreachable-question and cycle
  checks get read by a person. A drag-to-rewire builder that silently orphans a question is a
  worse tool than a diff, and the AC is about the words.
- **Tier-mix is measured, and refuses.** Both sides are medians this hospital actually booked
  on that channel. With no completed intakes on the target tier, the panel says so instead of
  pricing phone V2 off the kiosk's V2 intakes; unknown renders as *unchanged*, never as zero
  saving. A number here is what an operator would switch a channel's tier on.

## Deviations from spec

- Doc 03 §10's slot-template editor, editable message registry and voice-pack upload are
  unbuilt (see the AC checklist). The slot panel still answers with its deferred marker.
- Doc 03 §11's node-level abandonment report is still deferred for want of per-node
  timestamps — unchanged from S18E.

## Baseline fix (first task, per the session protocol)

`make test` was **not** green on arrival: `test_resend_cooldown_is_enforced` and
`test_deactivated_user_cannot_use_a_live_token` failed on the first run and passed on the
second. Root cause: `_in_cooldown` compared `OtpCode.created_at` — written by Postgres — to a
Python-side cutoff, so the window measured clock skew as much as elapsed time. Docker's VM
clock right after `make dev` restarts the containers was enough to disable the resend limiter;
in production the same drift would do the same thing. The cutoff is computed in the database
now, with a regression test that passes a deliberately skewed `now` and still expects the
refusal.

## Tests & evidence

- `make test`: backend **1082 passed** (1071 → +11), voice-gw 22, web typecheck + lint + 48
  conformance, Android 6 JVM. `make lang-qa` clean.
- New backend tests: `test_auth.py` (+1, the skewed clock), `test_admin.py` (+5 — publish→live
  on the check-in path, the four whole-document refusals, draft versioning + audit, rollback,
  an unparseable published bank falling through to the file), `test_checkin_grading.py` (+3 —
  frozen rules beat the bank, an invalid snapshot grades amber by hand, a frozen rule over
  free text is still refused), `test_analytics.py` (+2 — the tier-mix hand calculation and its
  refusal).
- New e2e: `web/e2e/admin.spec.ts` (project `admin`, `npm run e2e:admin`) — 4 tests, all
  passing against a live stack.
- Screenshots: `web/screenshots/s18l/` (6). Self-critique per doc 04 §5, and it earned its
  keep — two defects came out of reading them:
  - `02-tree-editor.png` — the severity select offered `soon`/`info`, values the schema does
    not have, so a `semi` flag displayed as `urgent` and the next save would have **silently
    promoted it**: the difference between a patient keeping her place in the queue and jumping
    it. Fixed to the two real `Priority` values.
  - `06-protocols.png` — all seven question sets drawn on marigold `.notice` cards, spending
    the accent colour on everything so nothing was louder than anything else. Now plain
    cards; the red/amber pills carry the alarm.
  - `01`/`03`/`04`/`05` — list, saved-draft, published and test-run states; the spine reads in
    ask order and the "nothing is live until you publish" line lands where the eye is.

## Known gaps / stubs introduced

- **The protocol bank is a table, but nothing is published to it** — `make seed` writes v1 as
  a draft, so `resolve_bank` still serves the file. Publishing is a clinical act and the bank
  is still clinically unreviewed (S21).
- **The tree editor cannot change tree structure** — by design, documented on the screen.
- **The protocol editor edits the document, not fields** — the reading view above it is the
  structured half; a per-rung form is possible now that the table exists.
- **`web/e2e/admin.spec.ts` publishes a real tree version each run** — it edits with a
  timestamped string so the edit is always a change. It leaves published versions behind on a
  dev database; `make seed` does not undo that (publishing an older version does).

## Commits
- d4671b7 — S 18L: compute the OTP resend cutoff in the database
- 62a5081 — S 18L: the check-in protocol bank becomes a table an admin can publish
- 9702656 — S 18L: the visual tree editor, and a protocol panel that publishes
- a8cf6d3 — S 18L: tier-mix what-if — measured, and willing to say it does not know
- (session close — this file, HANDOFF, STATE)
