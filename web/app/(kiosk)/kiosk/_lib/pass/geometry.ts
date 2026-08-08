// The boarding pass's fixed geometry (doc 23 §3/§4).
//
// **Every number that decides where something lands on the paper lives here.**
// That is the rule the module is built around: the pass has a predefined length
// and breadth, and a layout constant hiding in a component is how a pass starts
// growing a millimetre at a time until the summary spills off the end of it.
//
// Millimetres are the unit throughout — the SVG's user units are millimetres,
// the canvas rasteriser multiplies by `dotsPerMm`, and `@page` is declared in
// them. There is no pixel anywhere in the layout.

/** One paper size, and the printer class that eats it. */
export type PassGeometry = {
  /** Roll width. The pass is exactly this wide, always. */
  widthMm: number;
  /** Pass length. The pass is exactly this long, always — §3's whole point. */
  lengthMm: number;
  /** 8 dots/mm = 203dpi, which is the entire cheap-thermal market. */
  dotsPerMm: 8;
  /**
   * The margin, and — on thermal — the part of the paper the head physically
   * cannot reach. An 80mm roll is printed by a **72mm** head and a 58mm roll by
   * a 48mm one, so these two numbers are the same number: the layout's margin
   * is set to the unprintable edge, the content lives in the middle, and the
   * raster is exactly the printable strip (§3: 576 dots = 72 bytes a row).
   *
   * The browser path has no such limit and simply prints the margin as white.
   */
  marginMm: number;
  bands: BandHeights;
  type: TypeRamp;
  cells: CellWidths;
};

/** The identity band's internal grid (§4): a token cell on the left, a labelled
 *  stack on the right. Here rather than in the layout for the same reason as
 *  everything else in this file — one place decides where things land. */
export type CellWidths = {
  /** Width of the token cell. The numeral is shrunk to fit it, never the
   *  reverse. */
  tokenMm: number;
  /** Width of the label column inside the right-hand stack. */
  labelMm: number;
  /** Row pitch of the four labelled rows (Age/Sex, Mobile, UHC ID, Issued). */
  rowMm: number;
  /** Padding inside a band, above its first line. */
  padMm: number;
};

/**
 * The five bands, top to bottom (§4). A band never grows: missing data prints
 * `—` in its place rather than reflowing its neighbours.
 *
 * The one permitted reallocation is `urgent` → `summary`, decided once at
 * layout time from a fact that is already settled (whether the rule engine
 * fired), so the pass is still exactly `lengthMm` either way.
 */
export type BandHeights = {
  header: number;
  identity: number;
  urgent: number;
  summary: number;
  stub: number;
};

/** The type ramp (§4). Sizes are cap-to-baseline millimetres — `font-size` in
 *  SVG user units, which are millimetres here. */
export type TypeRamp = {
  /** The token numeral in the identity band. Still the biggest thing on paper.
   *  Shrunk at layout time if a four-digit token would not fit its cell — a
   *  pass that overflows is worse than a pass whose number is 2mm smaller. */
  token: number;
  /** The token repeated on the tear-off stub. */
  tokenStub: number;
  name: number;
  heading: number;
  /** Summary body text, and the line pitch it is set on. */
  body: number;
  bodyLine: number;
  /** Small-caps labels: TOKEN, MOBILE, UHC ID. */
  label: number;
};

/** The canonical pass. A real boarding pass is ~187–203mm × 79–83mm; we print
 *  portrait because a thermal roll feeds lengthwise. */
export const ROLL80: PassGeometry = {
  widthMm: 80,
  lengthMm: 200,
  dotsPerMm: 8,
  marginMm: 4,
  bands: { header: 16, identity: 36, urgent: 8, summary: 114, stub: 26 },
  type: {
    token: 18,
    tokenStub: 8,
    name: 5,
    heading: 3.5,
    body: 3.2,
    bodyLine: 4.2,
    label: 2.4,
  },
  cells: { tokenMm: 30, labelMm: 14, rowMm: 6, padMm: 2.5 },
};

/** A supported degradation, not a second design (§3): smaller type and fewer
 *  summary lines on the same five bands. The pilot should confirm an 80mm head. */
export const ROLL58: PassGeometry = {
  widthMm: 58,
  lengthMm: 180,
  dotsPerMm: 8,
  marginMm: 5,
  bands: { header: 14, identity: 32, urgent: 7, summary: 105, stub: 22 },
  type: {
    token: 13,
    tokenStub: 6,
    name: 4,
    heading: 3,
    body: 2.7,
    bodyLine: 3.6,
    label: 2.1,
  },
  cells: { tokenMm: 22, labelMm: 12, rowMm: 5.2, padMm: 2 },
};

export const PASS_GEOMETRIES = { roll80: ROLL80, roll58: ROLL58 } as const;
export type PassGeometryName = keyof typeof PASS_GEOMETRIES;

/**
 * The geometry this build prints on. `NEXT_PUBLIC_PASS_GEOMETRY=roll80|roll58`,
 * **default `roll80`** — and an unrecognised value falls back to it rather than
 * throwing, because a typo in a kiosk's env file must not be the reason a
 * patient leaves with no paper at all.
 */
export function passGeometry(name?: string): PassGeometry {
  const key = (name ?? process.env.NEXT_PUBLIC_PASS_GEOMETRY ?? "roll80") as PassGeometryName;
  return PASS_GEOMETRIES[key] ?? ROLL80;
}

/** The width available between the margins — the line length every text run is
 *  measured against. */
export function innerWidthMm(geo: PassGeometry): number {
  return geo.widthMm - 2 * geo.marginMm;
}

/** The top edge of each band, derived from the heights rather than written down
 *  twice. `urgent` is zero-height when the intake raised no red flag, and the
 *  8mm goes to the summary (§4). */
export function bandTops(
  geo: PassGeometry,
  urgent: boolean
): { header: number; identity: number; urgent: number; summary: number; stub: number; summaryHeight: number } {
  const b = geo.bands;
  const urgentHeight = urgent ? b.urgent : 0;
  const header = 0;
  const identity = header + b.header;
  const urgentTop = identity + b.identity;
  const summary = urgentTop + urgentHeight;
  const summaryHeight = b.summary + (urgent ? 0 : b.urgent);
  return {
    header,
    identity,
    urgent: urgentTop,
    summary,
    summaryHeight,
    stub: summary + summaryHeight,
  };
}

/**
 * The raster the thermal bridge receives: **576 × 1600 dots on ROLL80**, which
 * is 72 bytes per row and ~115 KB on the wire (§3) — one POST, sub-second over
 * localhost, and a 203dpi head prints raster at full paper speed.
 *
 * The width is the printable strip, not the paper: `widthMm - 2 × marginMm`.
 * The rasteriser therefore draws the SVG's middle `72mm` and drops the margins,
 * which is what the print head does anyway.
 */
export function rasterSize(geo: PassGeometry): {
  widthDots: number;
  heightDots: number;
  bytesPerRow: number;
} {
  const widthDots = Math.round(innerWidthMm(geo) * geo.dotsPerMm);
  return {
    widthDots,
    heightDots: Math.round(geo.lengthMm * geo.dotsPerMm),
    bytesPerRow: Math.ceil(widthDots / 8),
  };
}
