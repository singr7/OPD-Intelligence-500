// The pass's ESC/POS raster (doc 23 §6), in the style of `print.spec.ts`: pure
// byte-building, no printer. The rasteriser's DOM half — serialise, `<img>`,
// canvas — is not exercised here; everything downstream of the pixels is, which
// is where the byte-level mistakes live.
//
// **No printer has ever printed one of these.** These tests pin the command
// structure so a regression is caught here rather than on a jammed printer at
// 9am; whether a particular clone accepts the cut command and a 1600-row raster
// is doc 23 §11's list, and only a real kiosk can answer it.

import { expect, test } from "@playwright/test";

import { ROLL58, ROLL80, rasterSize } from "../app/(kiosk)/kiosk/_lib/pass/geometry";
import { escposRaster, packBits } from "../app/(kiosk)/kiosk/_lib/pass/raster";

/** A blank pass's worth of bits: every dot paper, nothing ink. */
function blank(widthDots: number, heightDots: number): Uint8Array {
  return new Uint8Array(Math.ceil(widthDots / 8) * heightDots);
}

/** RGBA for a `w × h` image, painted by a callback returning [r,g,b,a]. */
function pixels(w: number, h: number, paint: (x: number, y: number) => number[]): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) out.set(paint(x, y), (y * w + x) * 4);
  }
  return out;
}

const WHITE = [255, 255, 255, 255];
const BLACK = [0, 0, 0, 255];

// -- the GS v 0 envelope ------------------------------------------------------

test("the job initialises, rasters, feeds and cuts, in that order", () => {
  const { widthDots, heightDots } = rasterSize(ROLL80);
  const bytes = Array.from(escposRaster(blank(widthDots, heightDots), widthDots, heightDots));
  expect(bytes.slice(0, 2)).toEqual([0x1b, 0x40]); // ESC @
  expect(bytes.slice(2, 5)).toEqual([0x1d, 0x76, 0x30]); // GS v 0
  expect(bytes.slice(-6, -4)).toEqual([0x1b, 0x64]); // ESC d — feed
  expect(bytes.slice(-3)).toEqual([0x1d, 0x56, 0x01]); // GS V 1 — partial cut
});

test("the header declares 72 bytes a row and 1600 rows, which is the 80mm pass", () => {
  const { widthDots, heightDots } = rasterSize(ROLL80);
  expect([widthDots, heightDots]).toEqual([576, 1600]);
  const bytes = Array.from(escposRaster(blank(widthDots, heightDots), widthDots, heightDots));
  const [m, xL, xH, yL, yH] = bytes.slice(5, 10);
  expect(m).toBe(0x00); // normal density
  expect(xL + xH * 256).toBe(72); // bytes per row, not dots
  expect(yL + yH * 256).toBe(1600);
});

test("the 58mm degradation declares its own smaller raster", () => {
  const { widthDots, heightDots } = rasterSize(ROLL58);
  const bytes = Array.from(escposRaster(blank(widthDots, heightDots), widthDots, heightDots));
  const [, xL, xH, yL, yH] = bytes.slice(5, 10);
  expect(xL + xH * 256).toBe(48);
  expect(yL + yH * 256).toBe(1440);
});

test("the payload is exactly the bitmap, no padding and nothing lost", () => {
  const { widthDots, heightDots } = rasterSize(ROLL80);
  const bits = blank(widthDots, heightDots);
  bits[0] = 0xff;
  bits[bits.length - 1] = 0xff;
  const bytes = escposRaster(bits, widthDots, heightDots);
  // 2 init + 8 header + payload + 3 feed + 3 cut.
  expect(bytes.length).toBe(2 + 8 + 72 * 1600 + 3 + 3);
  expect(bytes[10]).toBe(0xff);
  expect(bytes[10 + bits.length - 1]).toBe(0xff);
});

test("the whole pass is one POST inside the budget the design costed", () => {
  const { widthDots, heightDots } = rasterSize(ROLL80);
  const bytes = escposRaster(blank(widthDots, heightDots), widthDots, heightDots);
  // ~115 KB (§3) — one POST, sub-second over localhost.
  expect(bytes.length).toBeLessThan(120_000);
});

test("a bitmap that is not the declared size is refused rather than sent short", () => {
  // A truncated raster does not fail loudly on a printer; it prints half a pass
  // and cuts. Better to never reach the wire.
  expect(() => escposRaster(new Uint8Array(10), 576, 1600)).toThrow(/expected/);
});

// -- thresholding and bit packing ---------------------------------------------

test("black is ink and white is paper, MSB first across the row", () => {
  // One black pixel at x=0 sets the top bit of the first byte; one at x=7 sets
  // the bottom bit of the same byte.
  const bits = packBits(pixels(8, 1, (x) => (x === 0 || x === 7 ? BLACK : WHITE)), 8, 1);
  expect(Array.from(bits)).toEqual([0b10000001]);
});

test("rows are padded to whole bytes, so 576 dots is 72 bytes and 570 would be 72 too", () => {
  expect(packBits(pixels(576, 2, () => WHITE), 576, 2).length).toBe(144);
  expect(packBits(pixels(570, 2, () => WHITE), 570, 2).length).toBe(144);
});

test("there is no dithering: mid grey lands on one side of the threshold", () => {
  // Thermal paper has no grey and a dithered tint prints as mud (§4), so every
  // pixel is decided, never scattered.
  const light = packBits(pixels(8, 1, () => [200, 200, 200, 255]), 8, 1);
  const dark = packBits(pixels(8, 1, () => [100, 100, 100, 255]), 8, 1);
  expect(Array.from(light)).toEqual([0x00]);
  expect(Array.from(dark)).toEqual([0xff]);
});

test("a transparent pixel is paper, not ink", () => {
  // The failure this pins is a real one: an SVG with no background rect
  // rasterises to alpha-zero pixels, and folding those the other way would post
  // a solid black 115 KB rectangle to the printer.
  const bits = packBits(pixels(8, 1, () => [0, 0, 0, 0]), 8, 1);
  expect(Array.from(bits)).toEqual([0x00]);
});
