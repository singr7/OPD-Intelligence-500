import s from "../kiosk.module.css";

// Dhara — the assistant persona (doc 03 §1a). Breathing always; the ring pulses
// while she is "speaking" (audio playing) so latency reads as thinking, never a
// bare spinner (doc 04 law 11). Aesthetic risk #1, executed and kept quiet.
export function AssistantAvatar({
  speaking,
  status,
  name = "Dhara",
}: {
  speaking: boolean;
  status?: string;
  name?: string;
}) {
  return (
    <div className={s.assistantCol}>
      <div
        className={`${s.avatar} ${speaking ? s.avatarSpeaking : ""}`}
        aria-hidden="true"
      >
        <span className={s.avatarRing} />
        <svg className={s.avatarFace} viewBox="0 0 96 96" fill="none">
          <circle cx="48" cy="40" r="26" fill="currentColor" opacity="0.14" />
          <circle cx="39" cy="38" r="4" fill="currentColor" />
          <circle cx="57" cy="38" r="4" fill="currentColor" />
          <path
            d="M36 50c4 5 20 5 24 0"
            stroke="currentColor"
            strokeWidth="4"
            strokeLinecap="round"
          />
          <path
            d="M28 30c4-8 36-8 40 0"
            stroke="currentColor"
            strokeWidth="4"
            strokeLinecap="round"
          />
        </svg>
      </div>
      <div className={s.avatarName}>{name}</div>
      {status ? <div className={s.avatarStatus}>{status}</div> : null}
    </div>
  );
}
