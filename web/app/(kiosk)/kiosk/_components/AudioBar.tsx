import s from "../kiosk.module.css";

// Audio is the primary channel; this bar is how a patient replays the question
// (doc 04 law 1). The waveform animates only while playing — a calm signal that
// the kiosk is speaking, matched to the avatar's ring.
export function AudioBar({
  playing,
  label,
  onReplay,
}: {
  playing: boolean;
  label: string;
  onReplay: () => void;
}) {
  return (
    <div className={`${s.audioBar} ${playing ? s.audioBarPlaying : ""}`}>
      <button className={s.replayBtn} onClick={onReplay} aria-label={label}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <path
            d="M8 5v14l11-7z"
            fill="currentColor"
          />
        </svg>
      </button>
      <span className={s.audioWaves} aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
        <span />
      </span>
      <span className={s.audioLabel}>{label}</span>
    </div>
  );
}
