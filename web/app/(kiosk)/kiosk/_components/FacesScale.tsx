import s from "../kiosk.module.css";

// The faces severity scale (doc 04 law 6, aesthetic risk #3): five coloured
// faces, each speaks when tapped. A `scale` node's numeric range is mapped onto
// the five faces, so the value saved is still the tree's number (the red-flag
// rules compare numbers, never a face) — the face is only how the patient points.
const FACES = [
  { color: "#2FA36B", mouth: "M16 30c3 4 13 4 16 0", brow: "" },
  { color: "#8FB93B", mouth: "M16 29c3 2 13 2 16 0", brow: "" },
  { color: "#E2B01F", mouth: "M16 30h16", brow: "" },
  { color: "#E2761F", mouth: "M16 32c3-4 13-4 16 0", brow: "M14 18l6 3M34 18l-6 3" },
  { color: "#C73E3E", mouth: "M16 33c3-5 13-5 16 0", brow: "M14 16l6 4M34 16l-6 4" },
];

export function FacesScale({
  min,
  max,
  value,
  onSelect,
}: {
  min: number;
  max: number;
  value: number | null;
  onSelect: (v: number, faceIndex: number) => void;
}) {
  const valueFor = (i: number) => Math.round(min + (i / 4) * (max - min));
  return (
    <div className={s.faces} role="radiogroup" aria-label="Severity">
      {FACES.map((f, i) => {
        const v = valueFor(i);
        const selected = value !== null && valueFor(i) === value;
        return (
          <button
            key={i}
            className={`${s.face} ${selected ? s.faceSelected : ""}`}
            onClick={() => onSelect(v, i)}
            data-testid="face"
            role="radio"
            aria-checked={selected}
            aria-label={`${v}`}
          >
            <svg viewBox="0 0 48 48" width="100%" height="100%">
              <circle cx="24" cy="24" r="21" fill={f.color} opacity="0.16" />
              <circle cx="24" cy="24" r="21" fill="none" stroke={f.color} strokeWidth="2.5" />
              <circle cx="18" cy="21" r="2.4" fill={f.color} />
              <circle cx="30" cy="21" r="2.4" fill={f.color} />
              <path d={f.mouth} stroke={f.color} strokeWidth="3" fill="none" strokeLinecap="round" />
              {f.brow ? (
                <path d={f.brow} stroke={f.color} strokeWidth="2.5" strokeLinecap="round" />
              ) : null}
              <text
                x="24"
                y="45"
                textAnchor="middle"
                fontSize="9"
                fontWeight="800"
                fill={f.color}
              >
                {v}
              </text>
            </svg>
          </button>
        );
      })}
    </div>
  );
}
