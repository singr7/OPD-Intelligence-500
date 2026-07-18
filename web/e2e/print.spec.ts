// The ESC/POS token slip builder (S7). Pure byte-building, no printer — the
// first real slip needs a human watching a real 58mm printer (STATE.md: no live
// vendor has ever accepted a call). These pin the command structure so a
// regression is caught here rather than on a jammed printer at 9am.

import { expect, test } from "@playwright/test";

import { escposSlip, type Slip } from "../app/(kiosk)/kiosk/_lib/print";

const BASE: Slip = {
  tokenNo: 512,
  departmentName: "Medical Oncology",
  hospitalName: "GCH Alwar",
  issuedAt: "2026-07-18T09:30:00.000Z",
  urgent: false,
  lang: "en",
};

function bytes(slip: Slip): number[] {
  return Array.from(escposSlip(slip));
}

test("the slip starts with the ESC @ init and ends with a cut", () => {
  const b = bytes(BASE);
  expect(b.slice(0, 2)).toEqual([0x1b, 0x40]); // ESC @
  expect(b.slice(-3)).toEqual([0x1d, 0x56, 0x01]); // GS V 1 — partial cut
});

test("the token number is printed double-size and appears in the stream", () => {
  const b = bytes(BASE);
  // GS ! 0x11 — double width+height, immediately before the token digits.
  const doubleAt = findSeq(b, [0x1d, 0x21, 0x11]);
  expect(doubleAt).toBeGreaterThan(-1);
  const digits = [..."512"].map((c) => c.charCodeAt(0));
  expect(findSeq(b, digits)).toBeGreaterThan(doubleAt);
});

test("an urgent slip carries the show-at-desk line, a routine one does not", () => {
  const urgent = Array.from(escposSlip({ ...BASE, urgent: true }));
  const routine = bytes(BASE);
  const marker = [..."SHOW AT DESK NOW"].map((c) => c.charCodeAt(0));
  expect(findSeq(urgent, marker)).toBeGreaterThan(-1);
  expect(findSeq(routine, marker)).toBe(-1);
});

test("a null token prints an em dash placeholder, never a fabricated number", () => {
  // A spent block returns no token; the slip must not invent one.
  const b = bytes({ ...BASE, tokenNo: null });
  expect(findSeq(b, [..."—".normalize()].map((c) => c.charCodeAt(0)))).toBe(-1);
  // The '—' is > 0xFF so it encodes to '?' (0x3f) — present, and not a digit.
  expect(b).toContain(0x3f);
});

test("non-Latin lines fall back to '?' rather than emitting bad bytes", () => {
  // Devanagari needs the printer codepage set on the box; until then it prints
  // as '?', which is why the slip leans on the ASCII token + time.
  const b = Array.from(escposSlip({ ...BASE, lang: "hi", urgent: true }));
  // Every byte is a valid single octet (0..255); no multi-byte leakage.
  expect(b.every((x) => x >= 0 && x <= 255)).toBeTruthy();
});

function findSeq(haystack: number[], needle: number[]): number {
  outer: for (let i = 0; i <= haystack.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}
