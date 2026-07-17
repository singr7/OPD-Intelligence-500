import s from "../kiosk.module.css";

// Progress as simple dots + "3 of 8" (doc 04 law 2). The count is approximate on
// a branching tree — position is derived from answers, so `total` is a hint, not a
// promise; we never show a false precise percentage.
export function ProgressDots({
  current,
  total,
  ofLabel,
}: {
  current: number;
  total: number;
  ofLabel: string;
}) {
  const shown = Math.max(total, current);
  return (
    <div className={s.progress}>
      <div className={s.dots} aria-hidden="true">
        {Array.from({ length: shown }).map((_, i) => (
          <span
            key={i}
            className={`${s.dot} ${
              i < current - 1 ? s.dotDone : i === current - 1 ? s.dotCurrent : ""
            }`}
          />
        ))}
      </div>
      <span className={s.progressLabel}>
        {current} {ofLabel} {shown}
      </span>
    </div>
  );
}
