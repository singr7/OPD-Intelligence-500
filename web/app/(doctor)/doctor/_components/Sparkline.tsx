// A check-in trendline (doc 03 §5: "symptom sparkline across cycles").
//
// Inline SVG, no chart library: this is one polyline and two dots, and a
// dependency that ships an axis engine to draw it would be the tail wagging the
// dog. Scored 0–10 like the tree's `scale` nodes, so the vertical axis is fixed
// rather than auto-fitted — a fatigue score that climbed 3→8 must not look the
// same as one that wobbled 3→4, which is exactly what auto-scaling does.

const W = 132;
const H = 34;
const PAD = 3;
const MAX = 10;

export function Sparkline({
  points,
  rising,
}: {
  points: { at: string; value: number }[];
  rising: boolean;
}) {
  if (points.length < 2) return null;

  const step = (W - PAD * 2) / (points.length - 1);
  const y = (v: number) => H - PAD - (Math.min(Math.max(v, 0), MAX) / MAX) * (H - PAD * 2);
  const coords = points.map((p, i) => [PAD + i * step, y(p.value)] as const);
  const d = coords.map(([x, yy], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${yy.toFixed(1)}`).join(" ");
  const last = coords[coords.length - 1];
  // Rising = worse for every symptom the trees score (pain, fatigue, nausea).
  const stroke = rising ? "var(--danger)" : "var(--primary)";

  return (
    <svg
      className="spark"
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      role="img"
      aria-label={`${points.map((p) => p.value).join(", ")} out of ${MAX}`}
    >
      <line x1={PAD} y1={y(0)} x2={W - PAD} y2={y(0)} stroke="var(--line)" strokeWidth="1" />
      <path d={d} fill="none" stroke={stroke} strokeWidth="2" strokeLinecap="round"
        strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="3.2" fill={stroke} />
    </svg>
  );
}
