// The production text measurer (doc 23 §6).
//
// `layoutPass` is pure and asks for widths through a callback; this is the
// implementation that answers with the same font stack the SVG renders in, via
// a canvas `measureText`. Tests hand the layout a linear fake instead, which is
// the entire reason fitment is provable without a browser.

import { FONT_STACK } from "./fonts";
import type { Measure, Weight } from "./layout";

/** Measure at 10 px per millimetre and divide. Canvas text metrics are floats,
 *  so this is not about precision — it is about staying well away from the
 *  hinting a 3px font would get, which is not proportional to a 30px one. */
const PX_PER_MM = 10;

let context: CanvasRenderingContext2D | null | undefined;

function canvasContext(): CanvasRenderingContext2D | null {
  if (context !== undefined) return context;
  if (typeof document === "undefined") {
    context = null;
    return context;
  }
  context = document.createElement("canvas").getContext("2d");
  return context;
}

/**
 * Widths in millimetres for the font the pass is actually drawn in.
 *
 * Falls back to a linear estimate when there is no canvas — server rendering,
 * and nothing else, because the token screen only exists after a patient has
 * tapped through an intake. The estimate is deliberately *generous* (0.62em
 * against a real Noto average nearer 0.55em): if the fallback is ever hit, the
 * pass under-fills its summary band rather than overrunning it.
 */
export function canvasMeasure(): Measure {
  const ctx = canvasContext();
  if (!ctx) return estimate;
  return (text: string, sizeMm: number, weight: Weight) => {
    ctx.font = fontString(sizeMm, weight);
    return ctx.measureText(text).width / PX_PER_MM;
  };
}

export const estimate: Measure = (text, sizeMm) => 0.62 * sizeMm * text.length;

function fontString(sizeMm: number, weight: Weight): string {
  return `${weight === "bold" ? 700 : 400} ${sizeMm * PX_PER_MM}px ${FONT_STACK}`;
}
