import s from "../kiosk.module.css";

// Numbers over sliders (doc 04 law 6): a big +/- stepper, the unit spoken aloud
// by the caller. min/max clamp to the node's range; the value saved is the number.
export function Stepper({
  min,
  max,
  unit,
  value,
  onChange,
}: {
  min: number;
  max: number;
  unit: string | null;
  value: number;
  onChange: (v: number) => void;
}) {
  const dec = () => onChange(Math.max(min, value - 1));
  const inc = () => onChange(Math.min(max, value + 1));
  return (
    <div className={s.stepper}>
      <button
        className={s.stepBtn}
        onClick={dec}
        disabled={value <= min}
        aria-label="less"
      >
        −
      </button>
      <div className={s.stepValue}>
        <div className={s.stepNum}>{value}</div>
        {unit ? <div className={s.stepUnit}>{unit}</div> : null}
      </div>
      <button
        className={s.stepBtn}
        onClick={inc}
        disabled={value >= max}
        aria-label="more"
      >
        +
      </button>
    </div>
  );
}
