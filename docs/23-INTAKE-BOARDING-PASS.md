# 23 — The Intake Boarding Pass

**Status:** designed 2026-08-08, **built 2026-08-08** (SESSION-PASS). This
document was the build brief; §12 records where the build departed from it and
why. Everything else below is as-built. **No printer has printed one yet** —
§11 is still the open list.

**Pilot requirement (verbatim intent):** after intake, the kiosk prints a slip
the size and shape of an airline boarding pass carrying the token number,
patient name, mobile number, gender, age, UHC ID (if any) and — with the most
real estate — the intake summary. The screen that precedes it shows the same
pass with a **Print / Re-print** action. It must print on the kiosk's thermal
printer *or* any ordinary attached printer, it must be fast, and the output has
a **predefined length and breadth** with deterministic fitment: the summary can
never push the pass longer or spill off it.

---

## 1. What exists today, and why it is not enough

- `web/app/(kiosk)/kiosk/_lib/print.ts` builds a raw ESC/POS **text** stream
  for a 58mm roll: token, department, two phrases. Two documented limits:
  Indic text encodes to `?` (Latin-1 only, printer codepage never set), and
  the length of the slip is whatever the text happens to be.
- The browser fallback is `window.print()` over the live token screen with an
  `@media print` block in `kiosk.module.css` — no `@page` size, so it emits an
  A4 page whose layout depends on the browser's print scaling.
- No slip today carries phone, age, sex, UHC ID, or one line of the summary.
- Honesty note carried forward from print.ts: **no printer has ever printed
  one of these.** That stays true until someone stands at the real kiosk
  (doc 12 go-live gates).

The requirement adds Indic-heavy summary text at a fixed geometry on two very
different printer classes. Text-mode ESC/POS cannot do that. The design below
replaces the *rendering* strategy while keeping the bridge/fallback plumbing
that already works.

## 2. Design in one paragraph

One pure **layout function** turns the intake data into a list of positioned
primitives (text runs, rules, boxes) inside a fixed 80mm × 200mm frame,
truncating the summary deterministically to the space it owns. One **SVG
component** renders those primitives — that single artifact is (a) the
on-screen preview, (b) the browser-printed page via `@page { size: 80mm 200mm }`,
and (c) rasterised in the browser to a 1-bit bitmap wrapped in ESC/POS
`GS v 0` raster commands for the thermal bridge. Raster mode is what fixes
Devanagari/Telugu on thermal — the printer receives pixels, not codepoints, so
shaping is done by the browser's font stack, and the thermal output is
pixel-identical to the preview the patient just saw. The bytes are built **at
token time, before the button is pressed**, so Print is a single local POST.

## 3. Geometry — the predefined length and breadth

Canonical pass: **80mm wide × 200mm long** (real boarding passes are
~187–203mm × 79–83mm; we print portrait because thermal rolls feed lengthwise).
All layout constants live in one place, in millimetres:

```ts
// _lib/pass/geometry.ts
export type PassGeometry = {
  widthMm: number; lengthMm: number;
  dotsPerMm: 8;                 // 203dpi class, the entire cheap-thermal market
  marginMm: number;             // 4 — inside the printable area of 72/48mm heads
};
export const ROLL80: PassGeometry = { widthMm: 80, lengthMm: 200, dotsPerMm: 8, marginMm: 4 };
export const ROLL58: PassGeometry = { widthMm: 58, lengthMm: 180, dotsPerMm: 8, marginMm: 3 };
```

`NEXT_PUBLIC_PASS_GEOMETRY=roll80|roll58` selects at build/config time,
**default `roll80`**. The pilot should buy/confirm an 80mm printer — 58mm is a
supported degradation (smaller type, fewer summary lines), not a second design.
At 8 dots/mm the 80mm pass rasterises to **576 × 1600 dots = 72 bytes × 1600
rows ≈ 115 KB** — one POST, sub-second over localhost, and 203dpi heads print
raster at full paper speed. That is the "fast" budget.

The browser path uses the same numbers: `@page { size: 80mm 200mm; margin: 0 }`
and the SVG placed at true size. On an A4/A5 office printer the pass prints
centred at exact physical size; on a driver that ignores `@page` it still
prints as one proportional block. Either way the *artifact* has one geometry.

## 4. Layout — the five bands (top to bottom, 200mm total)

Fixed band heights; a band never grows. Missing data prints `—` in place, it
never reflows neighbours. Labels are bilingual — selected kiosk language first,
English beneath in small caps — exactly like a real boarding pass, so the desk
can always read it even when the patient chose Telugu.

| Band | Height | Content |
|---|---|---|
| 1. Header | 16mm | Hospital name (bold) + department left; `OPD PASS` lozenge right; thin rule beneath. |
| 2. Identity | 36mm | Left cell: `TOKEN` label + the number at **~18mm digit height** — still the biggest thing on the pass. Right cell, stacked: patient name (bold, ~5mm), then a labelled grid: `Age/Sex`, `Mobile`, `UHC ID`, `Issued` (date + HH:MM). |
| 3. Urgent band | 8mm | Only when red flags exist: reversed (black bg) `** SHOW AT DESK NOW **` in lang + English. When absent, the 8mm goes to band 4 — the one permitted reallocation, decided at layout time, still deterministic. |
| 4. Summary | 114mm (122 without band 3) | Rule + `INTAKE SUMMARY` heading. Chief complaint as a bold headline line. Then answers as `question — answer` lines. **This is the max-real-estate band**; sizing and truncation rules in §5. |
| 5. Stub | 26mm | Dashed tear rule, then a compact repeat: token (large, ~8mm), name, department, issued time. The desk can tear and keep the stub; the patient keeps the summary. |

Type ramp (80mm): token 18mm/8mm(stub), name 5mm, section heading 3.5mm,
summary body 3.2mm on a 4.2mm line pitch, labels 2.4mm small caps. Everything
pure black on white — thermal has no grey, and dithered grey prints as mud, so
the design never uses it. Rules and reversed bands do the visual separation.

## 5. The summary band — filling it without overflowing it

Input: `complaint` plus `SummaryAnswer[]` from the kiosk rail (KioskApp.tsx),
which already carries the presentation-only `summary_role`. Standing invariant
(STATE.md): `summary_role` is presentation-only — this module reads it, and
must never feed anything back into traversal, red flags, or routing.

Deterministic fill, in `layoutPass()`:

1. Order: complaint headline; answers with a `summary_role` headline slot in
   slot order; remaining answers in the order they were answered.
2. Each item renders as one line `question — answer`; if it needs more, it may
   wrap to **at most 2 lines**, then ellipsises mid-answer. Wrapping is
   computed by the layout function with measured text widths (see §6), not by
   CSS — the primitive list already contains final line breaks.
3. Lines are placed until the band's line budget is spent (~25 items on 80mm).
   If items remain, the **last line is always reserved** for
   `+ N more answers — full record is with the doctor` (bilingual). The pass
   never silently drops content; it says it abbreviated.
4. Output height is asserted, not hoped: `layoutPass()` throws in dev/test if
   any primitive lands outside its band. A unit test feeds a 60-answer intake
   and asserts total length is exactly `lengthMm`.

## 6. Architecture — one layout, three outputs

New directory `web/app/(kiosk)/kiosk/_lib/pass/`:

```
geometry.ts   ROLL80/ROLL58 constants, env selection
layout.ts     layoutPass(data: PassData, geo, measure): PassLayout
              PassData = { tokenNo, name, age, sex, phone, uhcId, department,
                           hospital, issuedAt, urgent, lang, complaint, answers }
              PassLayout = { widthMm, lengthMm, primitives: Primitive[] }
              Primitive = text run (x, y, size, weight, align, maxWidth)
                        | hairline | dashedRule | reversedBand
PassSvg.tsx   PassLayout → <svg viewBox="0 0 80 200"> — mm are user units.
              Used by the preview (CSS-scaled) and by browser print (true size).
raster.ts     svgToEscpos(svgEl, geo): serialise SVG → blob URL → <img> →
              offscreen <canvas> at dots resolution → getImageData →
              threshold at ~55% luminance (no dithering) → GS v 0 wrap
              (xL xH yL yH + 72-byte rows) + FEED + partial CUT.
print.ts      printPass(bytes | fallback): POST octet-stream to
              NEXT_PUBLIC_PRINT_BRIDGE_URL with a 2s AbortController timeout;
              on failure/absence → window.print() over the mounted PassSvg.
              Returns "thermal" | "browser" | "skipped" like today.
```

Design notes the implementer must keep:

- **`layout.ts` is pure** and takes a `measure(text, sizeMm, weight) → widthMm`
  callback. Production passes a canvas-`measureText` implementation; tests pass
  a deterministic fake (e.g. 0.55 × size × chars). That is what makes
  truncation and fitment unit-testable in Node with no browser.
- **SVG-in-`<img>` rasterisation** is the trick that gets shaped Indic text
  onto a dumb thermal printer with zero new dependencies. Constraint: an SVG
  loaded via `<img>` resolves **system fonts only** — no webfonts, no external
  refs. `PassSvg` therefore uses plain family stacks
  (`"Noto Sans", "Noto Sans Devanagari", "Noto Sans Telugu", sans-serif`), and
  the kiosk box must have Noto installed (an apt one-liner; add to the kiosk
  provisioning checklist, doc 05/12). The preview uses the same stack, so a
  missing font is visible on screen before it is visible on paper.
- **Pre-render at token time.** When the token screen mounts, kick off
  `svgToEscpos` and hold the bytes in state. The Print button then does one
  fetch — perceived latency is the printer's own feed rate. Re-print re-sends
  the held bytes; it never re-renders (identical paper both times).
- Existing `escposSlip()`/`printSlip()` and their tests stay — deleted only
  after the pass has printed on the real kiosk. The token-screen button
  switches to the pass; the old path remains one import away as the fallback
  of record until then.

## 7. The screen

The existing `TokenScreen` keeps its one job — the number readable from three
metres (doc 04) — and gains the pass beside it:

- Two-pane on kiosk landscape: token numeral pane (unchanged, left) + a
  live `PassSvg` preview at ~55% scale (right), so the patient sees exactly
  what paper they are about to get.
- One button under the preview: **Print pass** → after a successful return,
  relabels **Re-print** (`printPass` result drives it; a "browser" result
  counts as printed). New i18n keys `printPass`, `reprintPass` in all four
  languages; native review of mr/te wording joins the existing review gate.
- `NEXT_PUBLIC_PASS_AUTOPRINT=1` prints once automatically on mount (kiosk
  mode); the button then reads Re-print from the start. Default off — a laptop
  demo must not pop a print dialog uninvited.
- The `@media print` block in `kiosk.module.css` changes to hide everything
  except the mounted `PassSvg` and to declare the `@page` size. The staff
  strip stays excluded (existing rule: staff identity never on patient paper).
- Offline intakes: all `PassData` is client-side at token time, so the pass
  prints identically with the server down — the offline token block already
  supplies the number. Nothing new is persisted; the IndexedDB retention
  invariant (synced rows must not keep names/answers) is untouched.

## 8. Privacy decisions (made, not open)

- Full mobile number prints. It is the patient's own document, matching what a
  paper OPD card carries; the desk uses it for lookup. Not masked.
- Full name prints (S-UX.6 already puts it on the slip and screen).
- The summary prints **answers, not red-flag reasons** — the urgent band says
  *show at desk now* and nothing about why, same rule as the public board.
- Staff/candidate identity never appears (existing print-CSS rule, kept).

## 9. Coordinator re-print — explicitly out of scope here

Re-print in this module means: same kiosk session, same screen, press again.
A desk-side re-print (patient lost the slip at hour 2) needs the pass data
server-side; today the summary lives only in the kiosk's client state, and an
offline intake has no server record until sync. That is a follow-on with real
retention questions — noted for a future session, not smuggled in.

## 10. Build order for the implementing session

1. `geometry.ts` + `layout.ts` with the fake-measure unit tests: band
   accounting, missing-field `—`, urgent-band reallocation, 2-line wrap +
   ellipsis, `+ N more` reservation, hard fitment assertion. (This is most of
   the thinking; do it first, TDD.)
2. `PassSvg.tsx`; wire preview into `TokenScreen`; Playwright screenshot of
   the preview against a seeded intake (goes in `web/screenshots/`).
3. Browser print path: print CSS + `@page`; verify by printing to PDF and
   checking the page box is 80×200mm.
4. `raster.ts` + `GS v 0` byte tests (header dims for 576×1600, row length 72,
   trailing FEED+CUT) in the style of `e2e/print.spec.ts`.
5. `print.ts` (bridge POST, 2s timeout, fallback), pre-render on mount,
   Print/Re-print state, autoprint flag, i18n keys ×4 languages.
6. Gates: `npm run build`, `tsc`, `eslint`, existing kiosk E2E green, new unit
   tests green. Session log + STATE.md + fresh HANDOFF per doc 07.

**Do not:** add a dependency (none is needed); touch traversal/red-flag logic;
send bytes anywhere but the localhost bridge; let any layout number live
outside `geometry.ts`/the type ramp; claim the printer path works — it has not
met a printer, and the doc that says so is this one.

## 11. What only the real kiosk can prove (go-live checklist additions)

- 80mm vs 58mm head confirmed; `NEXT_PUBLIC_PASS_GEOMETRY` set to match.
- `GS v 0` raster accepted by the actual printer model (near-universal, but
  the cut command and max raster height are the two clone quirks).
- Noto Sans + Devanagari + Telugu installed on the kiosk OS; preview and
  paper show shaped text, not tofu.
- Feed-to-cut time for the 200mm pass measured; if the head is slow, the
  answer is a better printer, not a shorter summary.
- Bridge daemon owns the USB printer and answers on 127.0.0.1 (existing
  `NEXT_PUBLIC_PRINT_BRIDGE_URL` contract, unchanged).

## 12. As built — where the implementation departed from this brief

Four things, all decided during SESSION-PASS and all in the code with the
reasoning beside them.

1. **The raster is the print head's width, not the paper's.** §3 costed
   576 × 1600 dots / 72 bytes a row, which is 72mm at 8 dots/mm — the printable
   strip of an 80mm roll, not the 80mm. So `marginMm` does double duty: it is
   the layout margin *and* the unprintable edge, the rasteriser draws the pass
   shifted left by it, and the margins fall off both sides exactly as they do on
   paper. `ROLL58`'s margin moved 3mm → 5mm to land on the standard 48mm head.

2. **The identity grid's four field labels are English only.** §4 asked for
   bilingual labels throughout. In the grid there is no room:
   `यूएचसी आईडी · UHC ID` measures ~26mm against a 14mm label column and would be
   fitted down to ~1.8mm, which prints as a smudge on a 203dpi head and serves
   neither reader. The bilingual budget is spent where the *patient* reads — the
   token label, the summary heading, the chief-complaint label and the urgent
   band — and the four administrative fields sit beside self-describing values
   in one language, which is what a real boarding pass does. §4's stated purpose
   (the desk can read a Telugu pass) is met by the English half either way.

3. **`layoutPass` throws in dev and stays quiet in production.** §5.4 asked for
   the assertion; the choice about *where* it fires is that a thrown layout
   error on a kiosk means a patient standing at a machine with no paper. In CI
   it fails the build, which is the order those two things should happen in.

4. **One `GS v 0` command carries the whole pass**, rather than chunking rows
   defensively. Chunking would cost the single-POST budget for a quirk §11 has
   not seen yet; if a clone turns out to cap raster height, that is the moment
   to chunk.

Also worth knowing: the pass pane made the token screen tall enough to overflow
the portrait tablet, and `.tokenScreen` centres with `overflow-y: auto` — a
centred flex box pushes its first child above the top edge where scrolling
cannot reach it. It is `justify-content: safe center` now, asserted by the
`pass-ui` tablet-matrix test. Any future pane added to that screen inherits both
the risk and the test.
