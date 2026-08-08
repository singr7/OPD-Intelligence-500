// The pass, drawn once (doc 23 §6).
//
// This component is the *only* renderer. The same element is:
//
//   (a) the preview beside the token numeral, CSS-scaled down;
//   (b) the browser-printed page, at true physical size under `@page`;
//   (c) the source the rasteriser draws onto a canvas for the thermal bridge.
//
// So "the paper does not match the preview" is not a bug that can happen here —
// there is one artifact and three ways of looking at it.
//
// SVG user units are millimetres: the viewBox is `0 0 80 200` and `layoutPass`
// emits millimetres, so nothing in this file converts anything.

import { forwardRef } from "react";

import { FONT_STACK, TRACKING } from "./fonts";
import type { PassLayout, Primitive } from "./layout";

export type PassSvgProps = {
  layout: PassLayout;
  /** `true` for the printed page (millimetre width/height attributes, so a
   *  browser prints it at physical size); `false` for the on-screen preview,
   *  which scales to its container. */
  trueSize?: boolean;
  className?: string;
  /** Accessible name — the preview is a picture of a document, and a patient
   *  using a screen reader is told what it is rather than read 25 answers. */
  title: string;
  testId?: string;
};

export const PassSvg = forwardRef<SVGSVGElement, PassSvgProps>(function PassSvg(
  { layout, trueSize = false, className, title, testId },
  ref
) {
  const { widthMm, lengthMm } = layout;
  return (
    <svg
      ref={ref}
      className={className}
      data-testid={testId}
      role="img"
      aria-label={title}
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${widthMm} ${lengthMm}`}
      {...(trueSize
        ? { width: `${widthMm}mm`, height: `${lengthMm}mm` }
        : { preserveAspectRatio: "xMidYMid meet" })}
    >
      {/* Opaque white, always. The rasteriser thresholds luminance, and a
          transparent background reads as black once it is drawn onto a canvas —
          which would send a solid 115 KB of ink to the printer. */}
      <rect x={0} y={0} width={widthMm} height={lengthMm} fill="#ffffff" />
      {layout.primitives.map((primitive, index) => (
        <Mark key={index} primitive={primitive} />
      ))}
    </svg>
  );
});

function Mark({ primitive: p }: { primitive: Primitive }) {
  if (p.kind === "fill") {
    return <rect x={p.x} y={p.y} width={p.w} height={p.h} fill="#000000" />;
  }
  if (p.kind === "rule") {
    return (
      <line
        x1={p.x1}
        y1={p.y}
        x2={p.x2}
        y2={p.y}
        stroke="#000000"
        strokeWidth={p.dashed ? 0.3 : 0.25}
        // A tear line, drawn as one: the desk keeps the stub below it.
        strokeDasharray={p.dashed ? "1.6 1.2" : undefined}
      />
    );
  }
  return (
    <text
      x={p.x}
      y={p.y}
      fontFamily={FONT_STACK}
      fontSize={p.size}
      fontWeight={p.weight === "bold" ? 700 : 400}
      // Pure black on white throughout. Thermal paper has no grey — a dithered
      // tint prints as mud — so every separation on this pass is a rule, a
      // reversed band, or type size (§4).
      fill={p.invert ? "#ffffff" : "#000000"}
      textAnchor={p.align === "left" ? "start" : p.align === "right" ? "end" : "middle"}
      letterSpacing={p.tracked ? p.size * TRACKING : undefined}
      xmlSpace="preserve"
    >
      {p.text}
    </text>
  );
}
