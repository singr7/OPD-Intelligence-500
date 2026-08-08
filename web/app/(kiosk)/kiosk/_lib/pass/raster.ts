// SVG → 1-bit bitmap → ESC/POS `GS v 0` raster (doc 23 §6).
//
// ## Why raster, when the old slip sent text
//
// `print.ts`'s text-mode stream encodes to Latin-1 and prints Devanagari and
// Telugu as `?` — a documented limit since S7, and one no codepage command
// really fixes for two scripts at once on a cheap printer. Raster mode ends the
// argument: **the printer receives pixels, not codepoints.** Shaping — the
// conjuncts, the matras, the Telugu vowel signs — is done by the browser's own
// text engine before a byte goes near the printer. It is also what makes the
// paper pixel-identical to the preview the patient just looked at.
//
// ## The seam in this file
//
// `svgToEscpos` needs a DOM (an `<img>`, a canvas, `getImageData`). Everything
// downstream of the pixels — thresholding, bit packing, the `GS v 0` header —
// is pure and unit-tested in Node, because that is where the byte-level
// mistakes live and a browser adds nothing to catching them.
//
// ## No printer has ever printed one of these
//
// Same honesty as the slip this supersedes. The command set is near-universal;
// doc 23 §11 lists what only the real kiosk can prove — the cut command and the
// maximum raster height per command are the two clone quirks.

import { rasterSize, type PassGeometry } from "./geometry";

const ESC = 0x1b;
const GS = 0x1d;

const INIT = [ESC, 0x40]; // ESC @ — reset
const FEED = (n: number) => [ESC, 0x64, n];
const CUT = [GS, 0x56, 0x01]; // GS V 1 — partial cut

/** Below this fraction of full luminance a pixel is ink. No dithering: thermal
 *  paper has no grey, and a dithered tint prints as mud (§4). */
const INK_THRESHOLD = 0.55;

/** Feed past the cutter before cutting, so the tear line is below the stub and
 *  not through it. */
const FEED_LINES = 4;

/**
 * Rasterise the mounted pass and wrap it as an ESC/POS job.
 *
 * Called at token time, before the Print button is pressed (§6), so pressing it
 * is one local POST and the perceived latency is the printer's own feed rate.
 */
export async function svgToEscpos(
  svg: SVGSVGElement,
  geo: PassGeometry
): Promise<Uint8Array> {
  const { widthDots, heightDots } = rasterSize(geo);
  const image = await loadSvg(svg);

  const canvas = document.createElement("canvas");
  canvas.width = widthDots;
  canvas.height = heightDots;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("pass raster: no 2d canvas context");

  // White first: an un-drawn pixel is paper, not ink.
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, widthDots, heightDots);

  // Draw the whole pass, shifted left by the margin, so the canvas holds
  // exactly the strip the print head can reach. The margins fall off both
  // edges — which is what happens on the paper anyway.
  const scale = geo.dotsPerMm;
  ctx.drawImage(
    image,
    -geo.marginMm * scale,
    0,
    geo.widthMm * scale,
    geo.lengthMm * scale
  );

  const { data } = ctx.getImageData(0, 0, widthDots, heightDots);
  return escposRaster(packBits(data, widthDots, heightDots), widthDots, heightDots);
}

/** Serialise the live SVG and load it as an image. The blob URL is revoked in
 *  both directions — a kiosk that prints all day would otherwise leak one
 *  object URL per pass. */
function loadSvg(svg: SVGSVGElement): Promise<HTMLImageElement> {
  const markup = new XMLSerializer().serializeToString(svg);
  const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml;charset=utf-8" }));
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("pass raster: the SVG failed to load as an image"));
    };
    image.src = url;
  });
}

/**
 * RGBA pixels → one bit per dot, MSB first, row-padded to whole bytes.
 *
 * A set bit is ink, which is the convention `GS v 0` expects. Alpha is folded
 * against white, so a transparent pixel is paper: the alternative is a pass
 * that arrives at the printer as a solid black rectangle.
 */
export function packBits(
  rgba: Uint8ClampedArray,
  widthDots: number,
  heightDots: number
): Uint8Array {
  const bytesPerRow = Math.ceil(widthDots / 8);
  const out = new Uint8Array(bytesPerRow * heightDots);
  for (let y = 0; y < heightDots; y++) {
    for (let x = 0; x < widthDots; x++) {
      const i = (y * widthDots + x) * 4;
      const alpha = rgba[i + 3] / 255;
      const luma =
        ((0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2]) / 255) * alpha +
        (1 - alpha);
      if (luma < INK_THRESHOLD) {
        out[y * bytesPerRow + (x >> 3)] |= 0x80 >> (x & 7);
      }
    }
  }
  return out;
}

/**
 * Wrap a packed bitmap in `GS v 0` and finish the job.
 *
 * `GS v 0 m xL xH yL yH d1..dk` — `m=0` is normal density, `xL/xH` is the row
 * length in **bytes**, `yL/yH` the number of rows. One command carries the
 * whole 576 × 1600 pass; a printer with a per-command raster-height limit is a
 * clone quirk for the real kiosk to find (§11), not something to pre-emptively
 * chunk around and lose the single-POST budget for.
 */
export function escposRaster(
  bits: Uint8Array,
  widthDots: number,
  heightDots: number
): Uint8Array {
  const bytesPerRow = Math.ceil(widthDots / 8);
  if (bits.length !== bytesPerRow * heightDots) {
    throw new Error(
      `pass raster: expected ${bytesPerRow * heightDots} bytes for ${widthDots}x${heightDots}, got ${bits.length}`
    );
  }
  const header = [
    GS,
    0x76,
    0x30,
    0x00,
    bytesPerRow & 0xff,
    (bytesPerRow >> 8) & 0xff,
    heightDots & 0xff,
    (heightDots >> 8) & 0xff,
  ];
  const trailer = [...FEED(FEED_LINES), ...CUT];
  const out = new Uint8Array(INIT.length + header.length + bits.length + trailer.length);
  out.set(INIT, 0);
  out.set(header, INIT.length);
  out.set(bits, INIT.length + header.length);
  out.set(trailer, INIT.length + header.length + bits.length);
  return out;
}
