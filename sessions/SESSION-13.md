# SESSION-13 — Multilingual completion (mr, te) + language QA harness

**Date:** 2026-07-25 · **Scope ref:** docs/06-BUILD-PLAN.md → S13

## Acceptance criteria checklist
- [x] **Full kiosk intake in all four languages.** The tree bank (11 trees), the
  kiosk shell (`web/.../i18n.ts`), the print slip and the offline read-back all
  carry en+hi+mr+te. `KioskLang` widened to `hi|en|mr|te`, so a missing UI string
  is a compile error. The language chooser lists मराठी and తెలుగు in their own script.
- [x] **Full WhatsApp intake in all four languages.** The three registered
  templates (`intake_invite`, `token_status`, `prescription_ready`) gained mr + te;
  the registry raises on a missing (name, lang) by design, so this is what lets an
  out-of-window Marathi/Telugu patient be messaged at all.
- [x] **Language QA harness in CI.** `app/lang_qa.py` — completeness across surfaces,
  script/no-English-leak, glossary consistency, STT/TTS round-trip + BCP-47 mapping.
  Runs as `make lang-qa`, as a named CI step, and as `tests/test_lang_qa.py`.
- [x] **Font/line-height audit (doc 04 §4).** Noto Sans Telugu self-hosted at build;
  font-family falls through Latin→Devanagari→Telugu; the ≥1.6 line-height that was
  hi-only now covers mr and te.

**AC caveat:** "full intake in all four languages" is proven structurally (the
harness + conformance + typecheck), not yet *visually* on a Telugu screen — the
next/font Telugu subset typechecks but no one has watched it render at 200% scale
on the box. Logged in STATE → Stubs (pairs with the other on-omen UI validation).

## What was built
- **`app/languages.py`** — `PILOT_LANGUAGES = (en,hi,mr,te)` as one source of truth
  (promoted out of the tree-bank test), plus `looks_like_script` / `SCRIPT_RANGES`.
- **mr/te across the tree bank** — 258 unique strings, applied by a single
  `en→(mr,te)` map (scratchpad migration) so a phrase repeated across routing trees
  is identical everywhere; the validator enforced per-string completeness on write.
- **mr/te for the kiosk shell, print slip, WhatsApp templates, offline read-back.**
  Three inline hi/en ternaries in `KioskApp` (generic error, offline-dept error,
  spoken token line) folded into the `T` table so mr/te stop falling back to English.
- **`app/lang_qa.py` + `seeds/glossary.json`** — the QA harness and its glossary
  (11 core symptom words, canonical per language).
- **Font wiring** — `layout.tsx` (Telugu font), `globals.css` (stack), `kiosk.module.css`
  (line-height for mr/te).
- **`make lang-qa`** target + a named CI step in the backend job.

## Decisions made
- **One `en→(mr,te)` map, applied programmatically, not per-file hand editing.**
  258 unique strings instead of 716 localized blocks, and cross-tree consistency for
  free — the same guarantee the glossary check then enforces going forward.
- **`PILOT_LANGUAGES` is now app code, not test code.** The seed, the tests and the
  harness must agree on what "complete" means; a copy in each would drift.
- **The harness checks script, not just presence.** "Is the `mr` key set?" passes on
  English pasted into the `mr` slot; "does it contain a Devanagari character?" does
  not. This is the check that actually catches an untranslated string.
- **mr/te are model-drafted, flagged pending native + clinical review at S21** — the
  same honest stance the hi text has carried since S4, made loud in HANDOFF and STATE.

## Deviations from spec
- None material. doc 04 §4 also wants a 200%-scale visual pass; that needs the stack
  up on a real screen and is deferred to the on-box validation (STATE → Stubs), not
  skipped — the font is wired and typechecks, only the eyeball is owed.

## Tests & evidence
- `make test`: **826** backend (816→826, +10 lang-qa), voice-gw 1, web typecheck+lint
  clean, 48 conformance. `make lang-qa`: clean across [en, hi, mr, te].
- New tests: `tests/test_lang_qa.py` (10) — one asserting the live repo is complete,
  the rest injecting each defect (missing lang, English-left-in-place, wrong script,
  synonym drift) to prove the harness goes red.
- Conformance fixtures regenerated (they embed the four-language tree JSON).
- Screenshots: none this session — S13 adds no new screen, only new language variants
  of existing ones; the visual proof that matters (Telugu rendering) is owed on the box.

## Known gaps / stubs introduced (mirrored to STATE.md)
- **mr/te are unreviewed** by a native speaker or clinician — the loudest gap; they
  must be read before a patient sees them (S21).
- **The Telugu kiosk has never been seen rendered** — font wired + typechecked, visual
  proof owed on the box (doc 04 §4's 200%-scale / line-height pass).
- **No mr/te STT/TTS has hit a live vendor** — round-trip is proven on the fakes and
  the BCP-47 map, same as every channel's first-vendor caveat.
- **The glossary consistency check is exact-block only** — it pins the canonical short
  forms (an option that is exactly "Fever"), not a term buried in a sentence; substring
  consistency would need fuzzier matching and is deliberately out of scope.

## Commits
- (1) S 13: mr + te across the whole tree bank — every node, option and red flag
- (2) S 13: mr + te for the WhatsApp templates and the offline read-back
- (3) S 13: the kiosk shell speaks four languages, and Telugu gets a font
- (4) S 13: the language QA harness — completeness, script, glossary, audio round-trip
- (close) S 13: session close
