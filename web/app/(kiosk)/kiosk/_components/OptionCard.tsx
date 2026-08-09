import s from "../kiosk.module.css";
import { Icon } from "../_lib/icons";

// The whole card is the tap target (doc 04 law 3), ≥64px, with a meaning-bearing
// duotone icon (law 4). Selection is shown by fill + a check, so a multi-select
// reads at a glance.
export function OptionCard({
  text,
  icon,
  selected,
  careSystem,
  onSelect,
}: {
  text: string;
  icon?: string | null;
  selected?: boolean;
  /** The department's system of medicine, when this card is a department (doc 24
   *  §5). Passed straight to the DOM and never read in TypeScript — the
   *  stylesheet keys off it, so no component compares against the value and the
   *  card of a third system would style itself by adding one CSS rule. */
  careSystem?: string;
  onSelect: () => void;
}) {
  return (
    <button
      className={`${s.optionCard} ${selected ? s.optionCardSelected : ""}`}
      onClick={onSelect}
      aria-pressed={selected}
      data-care-system={careSystem}
      data-testid="option"
    >
      <span className={s.optionIcon}>
        <Icon name={icon ?? undefined} title={text} />
      </span>
      <span className={s.optionText}>{text}</span>
      <svg className={s.optionCheck} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M5 13l4 4L19 7"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
