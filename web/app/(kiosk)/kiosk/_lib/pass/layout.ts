// `layoutPass()` — the intake data becomes a list of positioned primitives
// inside a fixed frame (doc 23 §5/§6).
//
// **This file is pure.** No DOM, no React, no `document`. It takes a `measure`
// callback, so production hands it a canvas `measureText` and a test hands it a
// deterministic fake — which is the whole reason truncation and fitment can be
// unit-tested in Node with no browser, and the reason a 60-answer intake can be
// proven not to push the pass past 200mm without anyone printing one.
//
// The output is consumed three ways from one place (§2): the on-screen preview,
// the browser-printed page, and the raster the thermal bridge receives. A
// difference between what the patient saw and what came out of the printer is
// therefore not representable.

import type { KioskLang } from "../i18n";
import type { SummaryRole } from "../tree/types";

import {
  bandTops,
  innerWidthMm,
  type PassGeometry,
} from "./geometry";
import {
  PASS_ABSENT,
  PASS_LOZENGE,
  PASS_MORE,
  PASS_URGENT,
  type PassLabelKey,
  bilingual,
  bilingualInline,
  fieldLabel,
} from "./labels";

/** Measured advance width of one run, in millimetres. Production measures with
 *  the same font stack the SVG renders in; tests use a linear fake. */
export type Measure = (text: string, sizeMm: number, weight: Weight) => number;

export type Weight = "normal" | "bold";
export type Align = "left" | "right" | "center";
export type BandName = "header" | "identity" | "urgent" | "summary" | "stub";

export type Primitive =
  | {
      kind: "text";
      band: BandName;
      x: number;
      /** Baseline, in millimetres from the top of the pass. */
      y: number;
      size: number;
      weight: Weight;
      align: Align;
      text: string;
      /** White ink, for the reversed urgent band. */
      invert?: boolean;
      /** Small-caps labels are tracked out; the renderer applies letter-spacing. */
      tracked?: boolean;
    }
  | { kind: "rule"; band: BandName; x1: number; x2: number; y: number; dashed?: boolean }
  | { kind: "fill"; band: BandName; x: number; y: number; w: number; h: number };

export type PassLayout = {
  widthMm: number;
  lengthMm: number;
  primitives: Primitive[];
};

/** One answer as the kiosk rail already holds it (KioskApp's `SummaryAnswer`).
 *  `role` is read for ordering only — the standing invariant is that
 *  `summary_role` is presentation-only and never feeds traversal, red flags or
 *  routing, and printing it on paper is as presentational as it gets. */
export type PassAnswer = {
  nodeId: string;
  role: SummaryRole | null;
  question: string;
  answer: string;
};

export type PassData = {
  tokenNo: number | null;
  name: string;
  age: number | null;
  sex: "male" | "female" | "other" | null;
  phone: string;
  uhcId: string;
  department: string;
  hospital: string;
  /** ISO instant the token was issued. */
  issuedAt: string;
  /** The rule engine fired. Drives the reversed band — never the reason. */
  urgent: boolean;
  lang: KioskLang;
  complaint: string;
  answers: PassAnswer[];
  /** Localised `male|female|other`, passed in so this file stays free of the
   *  kiosk's `T` table and the layout stays a pure function of its input. */
  sexLabels: Record<"male" | "female" | "other", string>;
};

/** The order headline answers are printed in (§5.1). A role not in this list
 *  sorts after all of them, in the order the patient answered. */
const HEADLINE_ROLES: SummaryRole[] = [
  "primary_symptom",
  "duration",
  "severity",
  "symptom_detail",
  "context",
];

/** An answer line may wrap this far and no further; then it ellipsises
 *  mid-answer (§5.2). Two lines is the point where one talkative answer starts
 *  costing another answer its place on the pass. */
const MAX_ANSWER_LINES = 2;

export function layoutPass(
  data: PassData,
  geo: PassGeometry,
  measure: Measure
): PassLayout {
  const out: Primitive[] = [];
  const inner = innerWidthMm(geo);
  const left = geo.marginMm;
  const right = geo.widthMm - geo.marginMm;
  const tops = bandTops(geo, data.urgent);
  const { type: ramp, cells } = geo;

  // -- band 1: header --------------------------------------------------------
  //
  // Hospital and department on the left, the document's own name on the right.
  // The lozenge is measured first because the hospital name is fitted to
  // whatever is left over — a long hospital name shrinks, it never collides.
  const lozengeW = measure(PASS_LOZENGE, ramp.label, "bold") + 4;
  const lozengeH = ramp.label * 2.4;
  const lozengeY = tops.header + cells.padMm;
  out.push({
    kind: "rule",
    band: "header",
    x1: right - lozengeW,
    x2: right,
    y: lozengeY,
  });
  out.push({
    kind: "rule",
    band: "header",
    x1: right - lozengeW,
    x2: right,
    y: lozengeY + lozengeH,
  });
  out.push({
    kind: "text",
    band: "header",
    x: right - lozengeW / 2,
    y: lozengeY + lozengeH * 0.72,
    size: ramp.label,
    weight: "bold",
    align: "center",
    text: PASS_LOZENGE,
    tracked: true,
  });

  const headerTextWidth = inner - lozengeW - 3;
  const hospital = fitLine(
    data.hospital,
    headerTextWidth,
    ramp.name,
    ramp.heading,
    "bold",
    measure
  );
  out.push({
    kind: "text",
    band: "header",
    x: left,
    y: tops.header + cells.padMm + hospital.size * 0.8,
    size: hospital.size,
    weight: "bold",
    align: "left",
    text: hospital.text,
  });
  const department = fitLine(
    data.department || PASS_ABSENT,
    headerTextWidth,
    ramp.body,
    ramp.label,
    "normal",
    measure
  );
  out.push({
    kind: "text",
    band: "header",
    x: left,
    y: tops.header + cells.padMm + hospital.size * 0.8 + ramp.bodyLine,
    size: department.size,
    weight: "normal",
    align: "left",
    text: department.text,
  });
  out.push({ kind: "rule", band: "header", x1: left, x2: right, y: tops.identity });

  // -- band 2: identity ------------------------------------------------------
  //
  // The token is still the biggest thing on the pass, and it is fitted to its
  // cell rather than allowed to run into the patient's name: a four-digit
  // offline token at 18mm would be 43mm wide on a 34mm cell, so it comes down a
  // couple of millimetres and stays legible from across a waiting room.
  const identityTop = tops.identity + cells.padMm;
  out.push({
    kind: "text",
    band: "identity",
    x: left,
    y: identityTop + ramp.label * 0.8,
    size: ramp.label,
    weight: "bold",
    align: "left",
    text: bilingualInline("token", data.lang),
    tracked: true,
  });
  const tokenText = data.tokenNo === null ? PASS_ABSENT : String(data.tokenNo);
  const token = fitLine(tokenText, cells.tokenMm, ramp.token, ramp.token * 0.6, "bold", measure);
  out.push({
    kind: "text",
    band: "identity",
    x: left,
    y: identityTop + ramp.label * 1.6 + token.size * 0.8,
    size: token.size,
    weight: "bold",
    align: "left",
    text: token.text,
  });

  const gridX = left + cells.tokenMm + 2;
  const gridW = right - gridX;
  const name = fitLine(
    data.name.trim() || PASS_ABSENT,
    gridW,
    ramp.name,
    ramp.body,
    "bold",
    measure
  );
  out.push({
    kind: "text",
    band: "identity",
    x: gridX,
    y: identityTop + name.size * 0.8,
    size: name.size,
    weight: "bold",
    align: "left",
    text: name.text,
  });

  const rows: [PassLabelKey, string][] = [
    ["ageSex", `${data.age ?? PASS_ABSENT} / ${data.sex ? data.sexLabels[data.sex] : PASS_ABSENT}`],
    ["mobile", data.phone.trim() || PASS_ABSENT],
    ["uhcId", data.uhcId.trim() || PASS_ABSENT],
    ["issued", formatIssued(data.issuedAt)],
  ];
  rows.forEach(([key, value], index) => {
    const y = identityTop + name.size * 1.4 + cells.rowMm * (index + 1);
    out.push({
      kind: "text",
      band: "identity",
      x: gridX,
      y,
      size: ramp.label,
      weight: "normal",
      align: "left",
      text: fieldLabel(key),
      tracked: true,
    });
    const valueW = gridW - cells.labelMm;
    const fitted = fitLine(value, valueW, ramp.body, ramp.label, "bold", measure);
    out.push({
      kind: "text",
      band: "identity",
      x: gridX + cells.labelMm,
      y,
      size: fitted.size,
      weight: "bold",
      align: "left",
      text: fitted.text,
    });
  });

  // -- band 3: urgent --------------------------------------------------------
  //
  // Reversed, and it says *go to the desk* without saying why — the reasons are
  // the doctor's, exactly as on the public board (§8).
  if (data.urgent) {
    out.push({
      kind: "fill",
      band: "urgent",
      x: 0,
      y: tops.urgent,
      w: geo.widthMm,
      h: geo.bands.urgent,
    });
    const urgentLines = data.lang === "en" ? [PASS_URGENT.en] : [PASS_URGENT[data.lang], PASS_URGENT.en];
    urgentLines.forEach((line, index) => {
      const size = index === 0 ? ramp.heading : ramp.label;
      const fitted = fitLine(line, inner, size, ramp.label * 0.8, "bold", measure);
      out.push({
        kind: "text",
        band: "urgent",
        x: geo.widthMm / 2,
        y:
          tops.urgent +
          (urgentLines.length === 1
            ? geo.bands.urgent * 0.68
            : geo.bands.urgent * (index === 0 ? 0.5 : 0.85)),
        size: fitted.size,
        weight: "bold",
        align: "center",
        text: fitted.text,
        invert: true,
      });
    });
  }

  // -- band 4: summary -------------------------------------------------------
  //
  // The max-real-estate band, and the only one whose content is not known in
  // advance. Everything above is a fixed number of lines; everything here is
  // fitted to what is left, and what does not fit is *counted*, never dropped
  // in silence.
  out.push({ kind: "rule", band: "summary", x1: left, x2: right, y: tops.summary });
  let cursor = tops.summary + cells.padMm;

  const headingLines = bilingual("summary", data.lang);
  headingLines.forEach((line, index) => {
    const size = index === 0 ? ramp.heading : ramp.label;
    cursor += size * 0.8;
    out.push({
      kind: "text",
      band: "summary",
      x: left,
      y: cursor,
      size,
      weight: "bold",
      align: "left",
      text: line,
      tracked: index > 0,
    });
    cursor += size * 0.5;
  });

  cursor += ramp.label * 0.8;
  out.push({
    kind: "text",
    band: "summary",
    x: left,
    y: cursor,
    size: ramp.label,
    weight: "normal",
    align: "left",
    text: bilingualInline("complaint", data.lang),
    tracked: true,
  });
  cursor += ramp.label * 0.5;

  const bandBottom = tops.stub;
  const complaintLines = wrapLine(
    data.complaint.trim() || PASS_ABSENT,
    inner,
    ramp.body,
    "bold",
    MAX_ANSWER_LINES,
    measure
  );
  complaintLines.forEach((line) => {
    cursor += ramp.bodyLine;
    out.push({
      kind: "text",
      band: "summary",
      x: left,
      y: cursor,
      size: ramp.body,
      weight: "bold",
      align: "left",
      text: line,
    });
  });
  cursor += ramp.bodyLine * 0.4;

  // What is left, in whole lines. Everything below counts against this budget
  // and nothing may exceed it — the assertion at the end of this function is
  // what makes that a fact rather than an intention.
  const budget = Math.max(0, Math.floor((bandBottom - cursor - cells.padMm) / ramp.bodyLine));
  const ordered = orderAnswers(data.answers);
  const wrapped = ordered.map((answer) =>
    wrapLine(
      `${answer.question} — ${answer.answer}`,
      inner,
      ramp.body,
      "normal",
      MAX_ANSWER_LINES,
      measure
    )
  );

  const total = wrapped.reduce((sum, lines) => sum + lines.length, 0);
  const placed: string[] = [];
  let printedItems = 0;
  if (total <= budget) {
    wrapped.forEach((lines) => placed.push(...lines));
    printedItems = wrapped.length;
  } else {
    // The last line of the band belongs to the "+ N more" notice, so the fill
    // stops one line short. Whole items only: half an answer on the paper is
    // worse than an honest count.
    for (const lines of wrapped) {
      if (placed.length + lines.length > budget - 1) break;
      placed.push(...lines);
      printedItems += 1;
    }
  }

  placed.forEach((line) => {
    cursor += ramp.bodyLine;
    out.push({
      kind: "text",
      band: "summary",
      x: left,
      y: cursor,
      size: ramp.body,
      weight: "normal",
      align: "left",
      text: line,
    });
  });

  const dropped = ordered.length - printedItems;
  if (dropped > 0) {
    cursor += ramp.bodyLine;
    const more = fitLine(
      PASS_MORE[data.lang].replace("{n}", String(dropped)),
      inner,
      ramp.body,
      ramp.label,
      "bold",
      measure
    );
    out.push({
      kind: "text",
      band: "summary",
      x: left,
      y: cursor,
      size: more.size,
      weight: "bold",
      align: "left",
      text: more.text,
    });
  }

  // -- band 5: stub ----------------------------------------------------------
  //
  // The desk tears this off and keeps it; the patient keeps the summary. Which
  // is why the stub repeats the token and the name and carries no clinical
  // content at all.
  out.push({ kind: "rule", band: "stub", x1: 0, x2: geo.widthMm, y: tops.stub, dashed: true });
  const stubTop = tops.stub + cells.padMm * 1.6;
  const stubToken = fitLine(tokenText, cells.tokenMm, ramp.tokenStub, ramp.body, "bold", measure);
  out.push({
    kind: "text",
    band: "stub",
    x: left,
    y: stubTop + stubToken.size * 0.8,
    size: stubToken.size,
    weight: "bold",
    align: "left",
    text: stubToken.text,
  });
  const stubName = fitLine(
    data.name.trim() || PASS_ABSENT,
    gridW,
    ramp.body,
    ramp.label,
    "bold",
    measure
  );
  out.push({
    kind: "text",
    band: "stub",
    x: gridX,
    y: stubTop + ramp.body * 0.8,
    size: stubName.size,
    weight: "bold",
    align: "left",
    text: stubName.text,
  });
  out.push({
    kind: "text",
    band: "stub",
    x: gridX,
    y: stubTop + ramp.body * 0.8 + ramp.bodyLine,
    size: ramp.label,
    weight: "normal",
    align: "left",
    text: department.text,
  });
  out.push({
    kind: "text",
    band: "stub",
    x: gridX,
    y: stubTop + ramp.body * 0.8 + ramp.bodyLine * 2,
    size: ramp.label,
    weight: "normal",
    align: "left",
    text: formatIssued(data.issuedAt),
  });

  const layout: PassLayout = {
    widthMm: geo.widthMm,
    lengthMm: geo.lengthMm,
    primitives: out,
  };
  assertFits(layout, geo, data.urgent, measure);
  return layout;
}

/** Headline slots first, in slot order; everything else in the order the
 *  patient answered it (§5.1). A stable sort over the original index keeps two
 *  answers with the same role in the order they were given. */
function orderAnswers(answers: PassAnswer[]): PassAnswer[] {
  const rank = (answer: PassAnswer) => {
    const index = answer.role ? HEADLINE_ROLES.indexOf(answer.role) : -1;
    return index === -1 ? HEADLINE_ROLES.length : index;
  };
  return answers
    .map((answer, index) => ({ answer, index }))
    .sort((a, b) => rank(a.answer) - rank(b.answer) || a.index - b.index)
    .map((entry) => entry.answer);
}

/**
 * Break one run into at most `maxLines`, at word boundaries, ellipsising the
 * last line mid-word if it still does not fit.
 *
 * The wrapping happens **here**, not in CSS: the primitive list already carries
 * its final line breaks, so the preview, the printed page and the raster cannot
 * disagree about where a line ended.
 */
export function wrapLine(
  text: string,
  maxWidth: number,
  size: number,
  weight: Weight,
  maxLines: number,
  measure: Measure
): string[] {
  if (measure(text, size, weight) <= maxWidth) return [text];

  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = "";
  for (let i = 0; i < words.length; i++) {
    if (lines.length === maxLines - 1) {
      // The last line this run is allowed. Everything still unplaced goes on it
      // and is cut to fit — this is the "ellipsises mid-answer" of §5.2, and it
      // is why an answer can lose its tail but never its place.
      const rest = [current, ...words.slice(i)].filter(Boolean).join(" ");
      lines.push(ellipsise(rest, maxWidth, size, weight, measure));
      return lines;
    }
    const candidate = current ? `${current} ${words[i]}` : words[i];
    if (measure(candidate, size, weight) <= maxWidth) {
      current = candidate;
    } else if (current) {
      lines.push(current);
      current = words[i];
    } else {
      // One word wider than the whole line — a long ID, or a script the
      // measurer sees as a single token. Cut it rather than overflow.
      lines.push(ellipsise(words[i], maxWidth, size, weight, measure));
      current = "";
    }
  }
  if (current) lines.push(ellipsise(current, maxWidth, size, weight, measure));
  return lines;
}

/** Trim a run until it and a trailing ellipsis fit. A pass that says the answer
 *  was longer than the space is honest; one that runs off the paper is not. */
export function ellipsise(
  text: string,
  maxWidth: number,
  size: number,
  weight: Weight,
  measure: Measure
): string {
  if (measure(text, size, weight) <= maxWidth) return text;
  let cut = text.length;
  while (cut > 0 && measure(`${text.slice(0, cut).trimEnd()}…`, size, weight) > maxWidth) {
    cut -= 1;
  }
  return `${text.slice(0, cut).trimEnd()}…`;
}

/** Shrink a run towards `minSize` until it fits, then ellipsise at `minSize`.
 *  Used for every value that owns a cell: the token, the hospital name, the
 *  patient's name, a phone number. */
export function fitLine(
  text: string,
  maxWidth: number,
  size: number,
  minSize: number,
  weight: Weight,
  measure: Measure
): { text: string; size: number } {
  let current = size;
  while (current > minSize && measure(text, current, weight) > maxWidth) {
    current = Math.max(minSize, current - 0.2);
  }
  if (measure(text, current, weight) <= maxWidth) return { text, size: current };
  return { text: ellipsise(text, maxWidth, current, weight, measure), size: current };
}

/**
 * The fitment assertion (§5.4). Every primitive must land inside the pass and
 * inside the band that claims it — output height is asserted, not hoped.
 *
 * It throws outside production, where a test or a developer sees it, and stays
 * quiet on a kiosk, where a thrown layout error would mean a patient standing
 * at a machine with no paper. On a kiosk the pass renders slightly wrong; in CI
 * it fails the build, which is the order those two things should happen in.
 */
function assertFits(
  layout: PassLayout,
  geo: PassGeometry,
  urgent: boolean,
  measure: Measure
): void {
  if (process.env.NODE_ENV === "production") return;
  const tops = bandTops(geo, urgent);
  const bounds: Record<BandName, [number, number]> = {
    header: [tops.header, tops.identity],
    identity: [tops.identity, tops.urgent],
    urgent: [tops.urgent, tops.summary],
    summary: [tops.summary, tops.stub],
    stub: [tops.stub, geo.lengthMm],
  };
  for (const p of layout.primitives) {
    const [top, bottom] = bounds[p.band];
    if (p.kind === "text") {
      const ascent = p.y - p.size * 0.8;
      const descent = p.y + p.size * 0.2;
      if (ascent < top - 0.01 || descent > bottom + 0.01) {
        throw new PassOverflowError(
          `"${p.text}" at y=${p.y.toFixed(1)}mm falls outside the ${p.band} band (${top}–${bottom}mm)`
        );
      }
      const width = measure(p.text, p.size, p.weight);
      const x1 = p.align === "left" ? p.x : p.align === "right" ? p.x - width : p.x - width / 2;
      if (x1 < -0.01 || x1 + width > geo.widthMm + 0.01) {
        throw new PassOverflowError(
          `"${p.text}" is ${width.toFixed(1)}mm wide and runs off a ${geo.widthMm}mm pass`
        );
      }
    } else {
      const bottomEdge = p.kind === "fill" ? p.y + p.h : p.y;
      if (p.y < top - 0.01 || bottomEdge > bottom + 0.01) {
        throw new PassOverflowError(
          `a ${p.kind} at y=${p.y.toFixed(1)}mm falls outside the ${p.band} band (${top}–${bottom}mm)`
        );
      }
    }
  }
}

export class PassOverflowError extends Error {
  constructor(message: string) {
    super(`pass layout overflow: ${message}`);
    this.name = "PassOverflowError";
  }
}

/** `DD/MM/YYYY HH:MM`, built by hand rather than through `toLocaleString`,
 *  which would make the same intake print differently on two kiosks and make
 *  this file's tests depend on the machine's locale. */
export function formatIssued(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return PASS_ABSENT;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
