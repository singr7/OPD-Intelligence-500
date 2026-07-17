import s from "../kiosk.module.css";

// The chief-complaint mic (doc 03 §1a: "big mic button, patient speaks freely;
// live waveform"). Marigold — this is the one big action on Q1. It halo-pulses
// while listening; a tap-to-type field sits beneath it as the always-present
// fallback (doc 04 law 8).
export function MicButton({
  listening,
  label,
  onPress,
}: {
  listening: boolean;
  label: string;
  onPress: () => void;
}) {
  return (
    <button
      className={`${s.micBtn} ${listening ? s.micListening : ""}`}
      onClick={onPress}
      aria-label={label}
      aria-pressed={listening}
    >
      <span className={s.micHalo} aria-hidden="true" />
      <svg className={s.micIcon} viewBox="0 0 48 48" fill="none">
        <rect x="18" y="6" width="12" height="24" rx="6" fill="currentColor" />
        <path
          d="M12 24a12 12 0 0024 0M24 36v6M17 42h14"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        />
      </svg>
    </button>
  );
}
