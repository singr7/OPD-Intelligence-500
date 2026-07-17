import s from "../kiosk.module.css";
import { OptionCard } from "./OptionCard";

// Body-map picker for pain location (doc 03 §1a). A tappable silhouette plus the
// option list beside it: the figure is the friendly affordance, the list is the
// authority so every option is always reachable (option ids are tree-specific, so
// the figure matches by keyword and falls back to the list for the rest).
type Region = { id: string; label: string; d: string; match: RegExp };

const REGIONS: Region[] = [
  { id: "head", label: "head", d: "M50 6a11 11 0 110 22 11 11 0 010-22z", match: /head|face|neck|throat/i },
  { id: "chest", label: "chest", d: "M34 30h32v22H34z", match: /chest|breast|heart|lung/i },
  { id: "abdomen", label: "abdomen", d: "M36 54h28v20H36z", match: /belly|abdom|stomach|gut/i },
  { id: "pelvis", label: "pelvis", d: "M38 76h24v14H38z", match: /pelvis|groin|hip|bladder/i },
  { id: "armL", label: "left arm", d: "M22 32h10v34H22z", match: /arm|shoulder|hand/i },
  { id: "legL", label: "left leg", d: "M40 92h8v26h-8z", match: /leg|knee|foot|thigh/i },
];

export function BodyMap({
  options,
  selected,
  onToggle,
}: {
  options: { id: string; text: string; icon: string | null }[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  const matchOption = (r: Region) =>
    options.find((o) => r.match.test(o.id) || r.match.test(o.text));

  return (
    <div className={s.bodyMap}>
      <svg className={s.bodyFig} viewBox="0 0 100 124" role="img" aria-label="Body">
        {/* silhouette */}
        <path
          d="M50 4a12 12 0 016 22c8 2 12 6 12 14v20l-4 18h-4l-2-16-2 40h-4l-2-30-2 30h-4l-2-40-2 16h-4l-4-18V42c0-8 4-12 12-14a12 12 0 016-24z"
          fill="var(--primary-soft)"
          opacity="0.5"
        />
        {REGIONS.map((r) => {
          const opt = matchOption(r);
          if (!opt) return null;
          const isSel = selected.includes(opt.id);
          return (
            <path
              key={r.id}
              d={r.d}
              className={`${s.bodyRegion} ${isSel ? s.bodyRegionSelected : ""}`}
              onClick={() => onToggle(opt.id)}
              aria-label={opt.text}
            />
          );
        })}
      </svg>
      <div className={s.bodyList}>
        {options.map((o) => (
          <OptionCard
            key={o.id}
            text={o.text}
            icon={o.icon}
            selected={selected.includes(o.id)}
            onSelect={() => onToggle(o.id)}
          />
        ))}
      </div>
    </div>
  );
}
