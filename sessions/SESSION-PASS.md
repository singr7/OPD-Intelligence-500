# SESSION-PASS — The intake boarding pass

**Date:** 2026-08-08 · **Scope ref:** `docs/23-INTAKE-BOARDING-PASS.md` (§10 build order)

A pilot requirement, arrived fully designed: after intake the kiosk prints a slip
the size and shape of an airline boarding pass, carrying the token, name, mobile,
age/sex, UHC ID and — with the most real estate — the intake summary, on a
thermal printer *or* any attached printer, fast, at a **predefined length and
breadth** the summary can never overrun.

## Acceptance criteria checklist

- [x] A fixed 80mm × 200mm pass whose length does not depend on the intake. A
      60-answer intake and an empty one produce the same 200mm; asserted, not hoped.
- [x] Token, name, mobile, age/sex, UHC ID and the summary all on one object,
      the summary in the largest band.
- [x] A Print / Re-print screen showing the same pass the printer will produce.
- [x] Thermal path: ESC/POS `GS v 0` raster over the existing local bridge.
- [x] Ordinary-printer path: `@page { size: 80mm 200mm }`, verified as an
      80 × 200mm MediaBox in a printed PDF.
- [x] Fast: bytes built at token time, so the button press is one local POST.
- [x] Indic text on thermal — as pixels, which is the only way it works.
- [x] Deterministic fitment: 2-line wrap, mid-answer ellipsis, a reserved
      `+ N more answers` line, and a hard band assertion.
- [x] All new patient-facing strings in four languages.
- [ ] **Nothing here has met a printer.** Unchanged from the brief; doc 23 §11.

## What was built

`web/app/(kiosk)/kiosk/_lib/pass/`:

- **`geometry.ts`** — `ROLL80` (default) and `ROLL58`, five band heights that
  sum to the pass, the type ramp, the identity grid's cells, `bandTops`,
  `rasterSize`. Every number that decides where something lands is in this file.
- **`layout.ts`** — `layoutPass(data, geo, measure)`, pure, returning positioned
  primitives (text runs, rules, a reversed fill) each tagged with the band that
  owns it. Plus `wrapLine`, `ellipsise`, `fitLine` and `assertFits`.
- **`labels.ts`** — the pass's own words in four languages, kept apart from the
  kiosk's `T` table because a boarding pass's register is not a conversation's.
- **`PassSvg.tsx`** — the one renderer. `viewBox="0 0 80 200"`, user units are
  millimetres, opaque white ground, pure black ink.
- **`measure.ts`** — the production canvas measurer, matching the render stack.
- **`fonts.ts`** — the system-font stack both of them must agree on.
- **`raster.ts`** — `svgToEscpos` (serialise → `<img>` → canvas → threshold) and
  the pure `packBits` / `escposRaster` beneath it.

`print.ts` gained `printPass` (bridge POST, 2s `AbortController`, browser
fallback). `KioskApp.tsx`'s `TokenScreen` gained the preview pane, the
pre-render on mount, Print/Re-print state and the autoprint flag. `i18n.ts`
gained `printPass`, `reprintPass`, `passPreview` ×4 languages.
`kiosk.module.css` gained the pane, the `@page` rule and a rewritten print block.

## Decisions made

1. **The raster is the print head's 72mm, not the paper's 80mm.** The brief's
   own costing (576 dots / 72 bytes a row) is the printable strip. So
   `marginMm` is both the layout margin and the unprintable edge, and the
   rasteriser draws the pass shifted left by it. `ROLL58`'s margin went 3 → 5mm
   to land on the standard 48mm head. **Do not "fix" the raster width to 640.**
2. **The identity grid's four field labels are English only.** Bilingual there
   measures ~26mm against a 14mm column and prints as a smudge. The bilingual
   budget is spent where the patient reads: token label, summary heading,
   complaint label, urgent band. Reasoning is in `labels.ts` beside the code.
3. **`assertFits` throws outside production and is silent inside it.** A thrown
   layout error on a kiosk is a patient with no paper; in CI it is a failed
   build. A test squashes the identity band to prove the guard is live.
4. **One `GS v 0` for the whole pass.** Defensive row-chunking would cost the
   single-POST budget for a clone quirk nobody has met yet.
5. **The issue time is frozen at mount.** A re-print re-sends the held bytes and
   is the same piece of paper, not a second render with a later timestamp.
6. **`escposSlip`/`printSlip` stay, with their tests.** They are the path of
   record until a real printer prints a pass (doc 23 §6). Only the button moved.

## Deviations from spec

Recorded permanently as **doc 23 §12** rather than only here: the raster width,
the English grid labels, where the assertion fires, and the single raster
command. Doc 23's status line now says built; doc 05 gained §6a (printer choice,
the Noto install the rasteriser depends on, the bridge).

## Tests & evidence

- **`make test` green**: backend **1,701**, voice-gw **25**, typecheck, lint,
  conformance **79** (was 48 — this session added 31).
- `npm run build` clean (with the dev server stopped first).
- **New pure suites**, both in the `conformance` project so they run in
  `make test`: `e2e/pass.spec.ts` (21 — band accounting, missing-field `—`,
  urgent-band reallocation, headline slot order, 2-line wrap + ellipsis,
  `+ N more`, the fitment assertion, determinism, geometry selection) and
  `e2e/pass-raster.spec.ts` (11 — the `GS v 0` envelope, header dimensions,
  payload integrity, thresholding, MSB packing, transparent-is-paper).
- **New browser project `pass-ui`** (`npm run e2e:pass`, 5 tests) — the session
  AC as a test. It proved the things the fake measurer cannot: that Print puts
  **115,206 bytes** of real ESC/POS on the bridge with a correct header and a
  sane ink count, that a second press sends byte-identical bytes, that the
  browser lays down one 80 × 200mm MediaBox, and that the preview is where a
  person can actually see it (`elementFromPoint`, not `toBeVisible`).
- Re-ran the projects the change could reach: `kiosk` 3, `ux-smoke` 2,
  `accessibility` 3, `assign` 3 — all green.
- **Screenshots** `web/screenshots/pass/`:
  - `01-token-with-pass.png` — the numeral keeps its pane and its job; the pass
    beside it is legibly a boarding pass (lozenge, tear rule, stub) rather than
    a generic card. First version was a 52vh thumbnail whose summary could not
    be read, which defeats the point; it is 62vh now.
  - `02-after-print.png` — the button reads "फिर से छापें".
  - `03-token-{1280x800,1024x768,800x1280}.png` — the tablet matrix.

## Known gaps / stubs introduced

- **No printer has printed one.** The rasterisation is proven end-to-end in a
  real browser; acceptance by a real print head is doc 23 §11 and is untouched.
- **Coordinator re-print is out of scope** (doc 23 §9) and stayed out: the pass
  data lives only in kiosk client state, and a desk-side re-print has real
  retention questions.
- **mr/te pass strings are model-drafted**, joining the existing native clinical
  review gate with the rest of S13's text.
- **`offline-demo` is red, and it was red before this session** — verified by
  running it against `75153fb` in a scratch worktree, where it fails at the same
  line (the downtime banner, before the token screen exists). Not this session's,
  and now in the backlog rather than lost.

## Commits

- `eed9d01` — S PASS: the pass has a length and a breadth, and a pure function that proves it
- `9be3245` — S PASS: one SVG is the preview, the printed page and the thermal raster
- `6f95fb0` — S PASS: drive it in a real browser, and fix what the portrait tablet showed
