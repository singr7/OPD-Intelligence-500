// The intake boarding pass's layout (doc 23 §5). Pure logic — no browser, no
// server, no printer — because `layoutPass` takes its text measurer as an
// argument. That is the whole design: the pass has a predefined length and
// breadth, and this file is where "the summary can never push the pass longer
// or spill off it" stops being an intention.
//
// The measurer here is deliberately linear and wrong-but-consistent
// (0.55 × size × characters). It is not trying to model a font; it is trying to
// make truncation and band accounting deterministic so a regression shows up
// here rather than on a jammed printer at 9am.

import { expect, test } from "@playwright/test";

import {
  ROLL58,
  ROLL80,
  bandTops,
  innerWidthMm,
  passGeometry,
  rasterSize,
} from "../app/(kiosk)/kiosk/_lib/pass/geometry";
import {
  PassOverflowError,
  layoutPass,
  wrapLine,
  type Measure,
  type PassAnswer,
  type PassData,
  type PassLayout,
  type Primitive,
} from "../app/(kiosk)/kiosk/_lib/pass/layout";

/** 0.55em per character. Every test in this file measures the same way, so a
 *  number here means "lines and millimetres", never "this font". */
const fake: Measure = (text, size) => 0.55 * size * text.length;

const SEX_LABELS = { male: "Male", female: "Female", other: "Other" };

function data(overrides: Partial<PassData> = {}): PassData {
  return {
    tokenNo: 512,
    name: "Kamla Devi",
    age: 54,
    sex: "female",
    phone: "+919812345678",
    uhcId: "OPD000001",
    department: "Medical Oncology",
    hospital: "Government Cancer Hospital, Alwar",
    issuedAt: "2026-08-08T09:30:00.000Z",
    urgent: false,
    lang: "en",
    complaint: "Lump in the right breast",
    answers: [],
    sexLabels: SEX_LABELS,
    ...overrides,
  };
}

function answers(n: number): PassAnswer[] {
  return Array.from({ length: n }, (_, i) => ({
    nodeId: `q${i}`,
    role: null,
    question: `Question ${i}`,
    answer: `Answer ${i}`,
  }));
}

function texts(layout: PassLayout, band?: string): string[] {
  return layout.primitives
    .filter((p): p is Extract<Primitive, { kind: "text" }> => p.kind === "text")
    .filter((p) => band === undefined || p.band === band)
    .map((p) => p.text);
}

// -- the predefined length and breadth ---------------------------------------

test("the pass is exactly its geometry, whatever the intake was", () => {
  for (const answerCount of [0, 1, 5, 25, 60]) {
    for (const urgent of [false, true]) {
      const layout = layoutPass(data({ answers: answers(answerCount), urgent }), ROLL80, fake);
      expect(layout.widthMm).toBe(80);
      expect(layout.lengthMm).toBe(200);
      const lowest = Math.max(
        ...layout.primitives.map((p) =>
          p.kind === "text" ? p.y + p.size * 0.2 : p.kind === "fill" ? p.y + p.h : p.y
        )
      );
      expect(lowest).toBeLessThanOrEqual(200);
    }
  }
});

test("a 60-answer intake fits the same 200mm as an empty one", () => {
  // The acceptance criterion from §5.4, stated the way the pilot stated it: the
  // summary can never make the pass longer.
  const long = layoutPass(data({ answers: answers(60) }), ROLL80, fake);
  const empty = layoutPass(data(), ROLL80, fake);
  expect(long.lengthMm).toBe(empty.lengthMm);
});

test("58mm is a supported degradation of the same five bands, not a second design", () => {
  const layout = layoutPass(data({ answers: answers(30) }), ROLL58, fake);
  expect(layout.widthMm).toBe(58);
  expect(layout.lengthMm).toBe(180);
  const bands = new Set(layout.primitives.map((p) => p.band));
  expect([...bands].sort()).toEqual(["header", "identity", "stub", "summary"]);
});

test("the bands sum to the pass, both with and without the urgent strip", () => {
  for (const geo of [ROLL80, ROLL58]) {
    for (const urgent of [false, true]) {
      const tops = bandTops(geo, urgent);
      expect(tops.stub + geo.bands.stub).toBe(geo.lengthMm);
      // The 8mm the urgent band does not take goes to the summary and nowhere
      // else — the one permitted reallocation (§4).
      expect(tops.summaryHeight).toBe(geo.bands.summary + (urgent ? 0 : geo.bands.urgent));
    }
  }
});

// -- missing data ------------------------------------------------------------

test("a field nobody gave prints an em dash and moves nothing", () => {
  const bare = layoutPass(
    data({ name: "  ", age: null, sex: null, phone: "", uhcId: "", complaint: "" }),
    ROLL80,
    fake
  );
  const full = layoutPass(data(), ROLL80, fake);
  expect(texts(bare, "identity")).toContain("— / —");
  expect(texts(bare, "identity").filter((line) => line === "—").length).toBeGreaterThanOrEqual(2);

  // Same primitive count, same y positions: a blank field is a value, not a gap.
  expect(bare.primitives.length).toBe(full.primitives.length);
  expect(bare.primitives.map((p) => p.y)).toEqual(full.primitives.map((p) => p.y));
});

test("a spent block prints an em dash where the token goes, never a number", () => {
  const layout = layoutPass(data({ tokenNo: null }), ROLL80, fake);
  // The token cell specifically — the identity band also carries a phone
  // number, which is all digits and entirely correct.
  const cell = layout.primitives.find(
    (p) => p.kind === "text" && p.band === "identity" && p.size === ROLL80.type.token
  );
  expect(cell?.kind === "text" && cell.text).toBe("—");
  // …and the stub the desk keeps does not invent one either.
  const stubCell = layout.primitives.find(
    (p) => p.kind === "text" && p.band === "stub" && p.size === ROLL80.type.tokenStub
  );
  expect(stubCell?.kind === "text" && stubCell.text).toBe("—");
});

// -- the urgent band ---------------------------------------------------------

test("the urgent band says go to the desk and never why", () => {
  const layout = layoutPass(
    data({ urgent: true, lang: "hi", complaint: "Bleeding since morning" }),
    ROLL80,
    fake
  );
  const urgent = texts(layout, "urgent");
  expect(urgent.join(" ")).toContain("SHOW AT DESK NOW");
  // Bilingual: the patient's language and the desk's, and the reversed fill
  // behind both.
  expect(urgent.length).toBe(2);
  expect(layout.primitives.some((p) => p.kind === "fill" && p.band === "urgent")).toBeTruthy();
  expect(
    layout.primitives.every((p) => p.kind !== "text" || p.band !== "urgent" || p.invert)
  ).toBeTruthy();
});

test("no red flag means no urgent band, and the summary gets the 8mm", () => {
  const routine = layoutPass(data({ answers: answers(60) }), ROLL80, fake);
  const urgent = layoutPass(data({ answers: answers(60), urgent: true }), ROLL80, fake);
  expect(texts(routine, "urgent")).toEqual([]);
  const lines = (layout: PassLayout) => texts(layout, "summary").length;
  // Two more body lines' worth of room, spent on answers.
  expect(lines(routine)).toBeGreaterThan(lines(urgent));
});

// -- the summary band --------------------------------------------------------

test("headline roles print in slot order, the rest in the order answered", () => {
  const layout = layoutPass(
    data({
      answers: [
        { nodeId: "c", role: null, question: "Anything else", answer: "No" },
        { nodeId: "b", role: "duration", question: "How long", answer: "Two months" },
        { nodeId: "a", role: "primary_symptom", question: "Main problem", answer: "Lump" },
        { nodeId: "d", role: null, question: "Travelled far", answer: "Yes" },
      ],
    }),
    ROLL80,
    fake
  );
  const body = texts(layout, "summary").filter((line) => line.includes(" — "));
  expect(body.map((line) => line.split(" — ")[0])).toEqual([
    "Main problem",
    "How long",
    "Anything else",
    "Travelled far",
  ]);
});

test("a long answer wraps to two lines and then ellipsises mid-answer", () => {
  const width = innerWidthMm(ROLL80);
  const long = `Describe the pain — ${"very ".repeat(60)}sharp`;
  const lines = wrapLine(long, width, ROLL80.type.body, "normal", 2, fake);
  expect(lines.length).toBe(2);
  expect(lines[1].endsWith("…")).toBeTruthy();
  for (const line of lines) {
    expect(fake(line, ROLL80.type.body, "normal")).toBeLessThanOrEqual(width);
  }
});

test("one unbreakable word longer than the line is cut, not overflowed", () => {
  const width = innerWidthMm(ROLL80);
  const [line] = wrapLine("X".repeat(400), width, ROLL80.type.body, "normal", 2, fake);
  expect(line.endsWith("…")).toBeTruthy();
  expect(fake(line, ROLL80.type.body, "normal")).toBeLessThanOrEqual(width);
});

test("what does not fit is counted on a reserved last line, never dropped in silence", () => {
  const layout = layoutPass(data({ answers: answers(60) }), ROLL80, fake);
  const summary = texts(layout, "summary");
  const last = summary[summary.length - 1];
  expect(last).toMatch(/^\+ \d+ more answers/);

  // The count is the truth: printed answers + counted answers = what the
  // patient actually answered.
  const printed = summary.filter((line) => /^Question \d+ — /.test(line)).length;
  const counted = Number(last.match(/^\+ (\d+)/)?.[1]);
  expect(printed + counted).toBe(60);
});

test("an intake that fits gets no more-line at all", () => {
  const layout = layoutPass(data({ answers: answers(4) }), ROLL80, fake);
  expect(texts(layout, "summary").some((line) => line.includes("more answers"))).toBeFalsy();
});

test("the chief complaint is always on the pass, however many answers follow", () => {
  const layout = layoutPass(
    data({ complaint: "Lump in the right breast", answers: answers(60) }),
    ROLL80,
    fake
  );
  expect(texts(layout, "summary")).toContain("Lump in the right breast");
});

test("the more-line speaks the patient's language", () => {
  const layout = layoutPass(data({ lang: "te", answers: answers(60) }), ROLL80, fake);
  const summary = texts(layout, "summary");
  expect(summary[summary.length - 1]).toContain("మరిన్ని");
});

// -- fitting things into cells ------------------------------------------------

test("a four-digit offline token shrinks to its cell rather than running into the name", () => {
  const three = layoutPass(data({ tokenNo: 512 }), ROLL80, fake);
  const four = layoutPass(data({ tokenNo: 1512 }), ROLL80, fake);
  const size = (layout: PassLayout, text: string) =>
    layout.primitives.find((p) => p.kind === "text" && p.text === text && p.band === "identity");
  const big = size(three, "512");
  const small = size(four, "1512");
  expect(big?.kind === "text" && big.size).toBe(ROLL80.type.token);
  expect(small?.kind === "text" && small.size).toBeLessThan(ROLL80.type.token);
  expect(small?.kind === "text" && fake("1512", small.size, "bold")).toBeLessThanOrEqual(
    ROLL80.cells.tokenMm
  );
});

// -- the stub ----------------------------------------------------------------

test("the stub tears off with the token and the name, and no clinical content", () => {
  const layout = layoutPass(
    data({ complaint: "Lump in the right breast", answers: answers(5) }),
    ROLL80,
    fake
  );
  const stub = texts(layout, "stub");
  expect(stub).toContain("512");
  expect(stub).toContain("Kamla Devi");
  expect(stub.join(" ")).not.toContain("Lump");
  expect(stub.join(" ")).not.toContain("Answer 0");
  expect(
    layout.primitives.some((p) => p.kind === "rule" && p.band === "stub" && p.dashed)
  ).toBeTruthy();
});

// -- the assertion itself -----------------------------------------------------

test("the fitment assertion is live: a band too small to hold its own content throws", () => {
  // Proving the guard rather than trusting it. A geometry whose identity band
  // cannot hold an 18mm numeral is exactly the mistake §5.4 exists to catch.
  const squashed = { ...ROLL80, bands: { ...ROLL80.bands, identity: 6, summary: 144 } };
  expect(() => layoutPass(data(), squashed, fake)).toThrow(PassOverflowError);
});

// -- determinism --------------------------------------------------------------

test("the same intake lays out identically twice — a re-print is the same paper", () => {
  const input = data({ answers: answers(30), urgent: true, lang: "hi" });
  expect(layoutPass(input, ROLL80, fake).primitives).toEqual(
    layoutPass(input, ROLL80, fake).primitives
  );
});

// -- configuration -------------------------------------------------------------

test("the geometry defaults to roll80 and an unknown name does not break the kiosk", () => {
  expect(passGeometry(undefined)).toBe(ROLL80);
  expect(passGeometry("roll58")).toBe(ROLL58);
  expect(passGeometry("roll-eighty-ish")).toBe(ROLL80);
});

test("the raster budget is the one the design costed: 576 x 1600 dots, 72 bytes a row", () => {
  // The width is the 72mm print head, not the 80mm paper — the margins are the
  // part of the roll the head cannot reach.
  expect(rasterSize(ROLL80)).toEqual({ widthDots: 576, heightDots: 1600, bytesPerRow: 72 });
  expect(rasterSize(ROLL58)).toEqual({ widthDots: 384, heightDots: 1440, bytesPerRow: 48 });
});
