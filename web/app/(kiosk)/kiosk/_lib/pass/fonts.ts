/**
 * The pass's font stack, shared by the renderer and the measurer.
 *
 * Plain system-font families, and that is a constraint rather than a
 * preference. An SVG loaded into an `<img>` — which is how the rasteriser gets
 * pixels onto a canvas — resolves **system fonts only**: no webfonts, no
 * `@font-face`, no external references. The kiosk's self-hosted Noto (S13) is
 * invisible from in there, so the pass asks for Noto by name and the kiosk box
 * must have it installed (doc 23 §11 puts it on the provisioning checklist).
 *
 * The preview uses the same stack, which is the useful half of the deal: a box
 * missing Noto Telugu shows tofu on screen, in front of a person who can do
 * something about it, before it shows tofu on a patient's paper.
 *
 * `measure.ts` must ask the canvas for this exact string, or the widths the
 * layout fits against are not the widths the renderer draws.
 */
export const FONT_STACK =
  '"Noto Sans", "Noto Sans Devanagari", "Noto Sans Telugu", sans-serif';

/** Tracking on small-caps labels, as a fraction of the type size. Kept small
 *  deliberately: `layoutPass` measures without tracking, so anything generous
 *  here is width the fitment assertion never saw. */
export const TRACKING = 0.06;
