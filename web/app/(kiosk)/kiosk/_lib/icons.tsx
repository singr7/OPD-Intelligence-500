// Duotone icon set (doc 04 law 4: every option a friendly, culturally neutral
// duotone icon). This ships a branded subset covering the high-frequency tree
// meanings; the full ~65-key custom set + human review is a design-asset task
// (S7/S21). Unknown keys fall back to a neutral duotone dot rather than breaking
// the law silently — an option is never iconless.
//
// Each icon draws two tones: a soft filled shape (`.soft`) and a stroked accent
// (`currentColor`), so it reads at a glance in bright OPD light.
import * as React from "react";

type P = { title?: string };

const wrap = (children: React.ReactNode, title?: string) => (
  <svg
    viewBox="0 0 48 48"
    width="100%"
    height="100%"
    fill="none"
    role="img"
    aria-label={title}
    aria-hidden={title ? undefined : true}
  >
    {children}
  </svg>
);

const soft = "var(--primary-soft)";
const ink = "var(--primary)";

const ICONS: Record<string, (p: P) => React.ReactNode> = {
  belly: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="26" r="14" fill={soft} />
        <circle cx="24" cy="26" r="4" stroke={ink} strokeWidth="2.5" />
        <path d="M24 12c-3 4-3 6 0 10" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  chest: ({ title }) =>
    wrap(
      <>
        <path d="M10 16h28v16a10 10 0 01-14 9 10 10 0 01-14-9V16z" fill={soft} />
        <path d="M24 20v18M16 24h16" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  head: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="20" r="12" fill={soft} />
        <path d="M18 20a6 6 0 0112 0M24 26v6" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  fever: ({ title }) =>
    wrap(
      <>
        <rect x="20" y="8" width="8" height="24" rx="4" fill={soft} />
        <circle cx="24" cy="36" r="7" fill={soft} />
        <path d="M24 16v16" stroke="var(--danger)" strokeWidth="3" strokeLinecap="round" />
        <circle cx="24" cy="36" r="4" fill="var(--danger)" />
      </>,
      title
    ),
  food: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="26" r="14" fill={soft} />
        <circle cx="24" cy="26" r="7" stroke={ink} strokeWidth="2.5" />
        <path d="M10 12v10M38 12v10" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  sleep: ({ title }) =>
    wrap(
      <>
        <path d="M34 26a12 12 0 11-14-14 10 10 0 0014 14z" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M30 12h6l-6 6h6" stroke={ink} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </>,
      title
    ),
  heart: ({ title }) =>
    wrap(
      <path
        d="M24 38S10 30 10 20a7 7 0 0114-2 7 7 0 0114 2c0 10-14 18-14 18z"
        fill={soft}
        stroke={ink}
        strokeWidth="2.5"
        strokeLinejoin="round"
      />,
      title
    ),
  lungs: ({ title }) =>
    wrap(
      <>
        <path d="M22 10v10c0 4-4 4-6 8s-2 10 2 10 6-4 6-10V10z" fill={soft} stroke={ink} strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M26 10v18c0 6 2 10 6 10s4-6 2-10-6-4-6-8V10z" fill={soft} stroke={ink} strokeWidth="2.5" strokeLinejoin="round" />
      </>,
      title
    ),
  cough: ({ title }) =>
    wrap(
      <>
        <path d="M20 30a8 8 0 118-8" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M32 20l6-3M32 26l7 1M32 32l6 4" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  blood: ({ title }) =>
    wrap(
      <path
        d="M24 8c6 8 10 13 10 18a10 10 0 01-20 0c0-5 4-10 10-18z"
        fill="var(--danger-soft)"
        stroke="var(--danger)"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />,
      title
    ),
  drop: ({ title }) =>
    wrap(
      <path
        d="M24 10c5 7 9 11 9 16a9 9 0 01-18 0c0-5 4-9 9-16z"
        fill={soft}
        stroke={ink}
        strokeWidth="2.5"
        strokeLinejoin="round"
      />,
      title
    ),
  pill: ({ title }) =>
    wrap(
      <>
        <rect x="8" y="20" width="32" height="16" rx="8" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M24 20v16" stroke={ink} strokeWidth="2.5" />
      </>,
      title
    ),
  bone: ({ title }) =>
    wrap(
      <path
        d="M16 20a4 4 0 10-4 4l8 8a4 4 0 104 4l-8-8"
        fill={soft}
        stroke={ink}
        strokeWidth="2.5"
        strokeLinejoin="round"
      />,
      title
    ),
  hand: ({ title }) =>
    wrap(
      <path
        d="M16 24V14a3 3 0 016 0v8m0 0v-12a3 3 0 016 0v12m0 0v-8a3 3 0 016 0v14a10 10 0 01-10 10c-6 0-8-4-12-8"
        fill={soft}
        stroke={ink}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />,
      title
    ),
  mouth: ({ title }) =>
    wrap(
      <>
        <path d="M10 24s6-8 14-8 14 8 14 8-6 8-14 8-14-8-14-8z" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M14 24h20" stroke={ink} strokeWidth="2.5" />
      </>,
      title
    ),
  ear: ({ title }) =>
    wrap(
      <path
        d="M18 34c-4-2-6-8-6-14a10 10 0 0120 0c0 4-4 6-6 9s0 6-4 6-2-4-4-1z"
        fill={soft}
        stroke={ink}
        strokeWidth="2.5"
        strokeLinejoin="round"
      />,
      title
    ),
  skin: ({ title }) =>
    wrap(
      <>
        <rect x="10" y="10" width="28" height="28" rx="8" fill={soft} />
        <circle cx="19" cy="20" r="2" fill={ink} />
        <circle cx="30" cy="26" r="2.5" fill={ink} />
        <circle cx="22" cy="31" r="1.6" fill={ink} />
      </>,
      title
    ),
  vomit: ({ title }) =>
    wrap(
      <>
        <path d="M16 14a8 8 0 0116 0v6H16z" fill={soft} stroke={ink} strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M20 24v8M24 24v12M28 24v8" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  breast: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="26" r="13" fill={soft} stroke={ink} strokeWidth="2.5" />
        <circle cx="24" cy="26" r="3" fill={ink} />
      </>,
      title
    ),
  lump: ({ title }) =>
    wrap(
      <>
        <path d="M10 30a14 14 0 0128 0z" fill={soft} stroke={ink} strokeWidth="2.5" strokeLinejoin="round" />
        <circle cx="24" cy="22" r="6" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="2.5" />
      </>,
      title
    ),
  needle: ({ title }) =>
    wrap(
      <path
        d="M12 36l14-14m0 0l4-4a4 4 0 016 6l-4 4m-6-6l6 6M18 30l-4 2 2-4"
        stroke={ink}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill={soft}
      />,
      title
    ),
  clock: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="24" r="14" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M24 16v8l6 4" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  scale: ({ title }) =>
    wrap(
      <>
        <path d="M10 34a14 14 0 0128 0z" fill={soft} stroke={ink} strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M24 34l6-10" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />
      </>,
      title
    ),
  alert: ({ title }) =>
    wrap(
      <>
        <path d="M24 8l16 28H8z" fill="var(--danger-soft)" stroke="var(--danger)" strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M24 18v8" stroke="var(--danger)" strokeWidth="3" strokeLinecap="round" />
        <circle cx="24" cy="31" r="1.8" fill="var(--danger)" />
      </>,
      title
    ),
  ok: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="24" r="15" fill={soft} />
        <path d="M16 24l6 6 11-12" stroke={ink} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </>,
      title
    ),
  stop: ({ title }) =>
    wrap(
      <>
        <rect x="10" y="10" width="28" height="28" rx="8" fill="var(--danger-soft)" />
        <path d="M18 18l12 12M30 18L18 30" stroke="var(--danger)" strokeWidth="3" strokeLinecap="round" />
      </>,
      title
    ),
  voice: ({ title }) =>
    wrap(
      <>
        <rect x="20" y="8" width="8" height="20" rx="4" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M14 24a10 10 0 0020 0M24 34v4" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  question: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="24" r="15" fill={soft} />
        <path d="M20 20a4 4 0 118 1c-1 2-4 2-4 5" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="24" cy="33" r="1.8" fill={ink} />
      </>,
      title
    ),
  body: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="12" r="5" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M24 18v14m0-10l-9 4m9-4l9 4m-9 10l-5 8m5-8l5 8" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  // Department icons.
  "iv-drip": ({ title }) =>
    wrap(
      <>
        <rect x="20" y="8" width="8" height="14" rx="3" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M24 22v8m0 0c0 4-3 4-3 8a3 3 0 006 0c0-4-3-4-3-8z" fill="var(--accent-soft)" stroke={ink} strokeWidth="2.5" />
      </>,
      title
    ),
  radiation: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="24" r="15" fill={soft} />
        <circle cx="24" cy="24" r="3.5" fill={ink} />
        <path d="M24 24l7-12a14 14 0 00-14 0zM24 24l14 0a14 14 0 00-7-12zM24 24l-14 0a14 14 0 007 12z" fill={ink} opacity=".5" />
      </>,
      title
    ),
  scalpel: ({ title }) =>
    wrap(
      <path
        d="M12 36l16-16 8-8 2 2-8 8-16 16z"
        fill={soft}
        stroke={ink}
        strokeWidth="2.5"
        strokeLinejoin="round"
      />,
      title
    ),
  "hands-holding": ({ title }) =>
    wrap(
      <>
        <path d="M8 26c4 6 8 8 16 8s12-2 16-8" fill={soft} stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
        <path d="M24 22a5 5 0 100-8 5 5 0 000 8z" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="2.5" />
      </>,
      title
    ),
  stethoscope: ({ title }) =>
    wrap(
      <>
        <path d="M16 10v10a8 8 0 0016 0V10" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
        <path d="M24 28v4a6 6 0 0012 0" stroke={ink} strokeWidth="2.5" strokeLinecap="round" fill="none" />
        <circle cx="36" cy="30" r="4" fill={soft} stroke={ink} strokeWidth="2.5" />
      </>,
      title
    ),
  gynae: ({ title }) =>
    wrap(
      <>
        <circle cx="24" cy="18" r="9" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M24 27v12M18 33h12" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  report: ({ title }) =>
    wrap(
      <>
        <rect x="12" y="8" width="24" height="32" rx="4" fill={soft} stroke={ink} strokeWidth="2.5" />
        <path d="M18 18h12M18 24h12M18 30h8" stroke={ink} strokeWidth="2.5" strokeLinecap="round" />
      </>,
      title
    ),
  dot: ({ title }) => wrap(<circle cx="24" cy="24" r="10" fill={soft} stroke={ink} strokeWidth="2.5" />, title),
};

// Synonym map — many tree keys share one drawn glyph until the full set exists.
const ALIAS: Record<string, string> = {
  thermometer: "fever",
  fire: "fever",
  bolt: "fever",
  hot: "fever",
  plate: "food",
  "plate-empty": "food",
  "plate-half": "food",
  "plate-full": "food",
  water: "drop",
  "water-low": "drop",
  sugar: "drop",
  moon: "sleep",
  tired: "sleep",
  loose: "vomit",
  wave: "voice",
  sound: "voice",
  wind: "cough",
  smoke: "cough",
  "pill-off": "pill",
  tube: "needle",
  bp: "heart",
  pulse: "heart",
  arm: "hand",
  leg: "bone",
  back: "body",
  pelvis: "gynae",
  twist: "body",
  grow: "lump",
  pain: "alert",
  block: "stop",
  empty: "dot",
  dots: "dot",
  unknown: "question",
  paper: "report",
  folder: "report",
  palette: "skin",
  "scale-down": "scale",
  "scale-flat": "scale",
  "scale-up": "scale",
};

export function iconFor(key: string | null | undefined): (p: P) => React.ReactNode {
  if (!key) return ICONS.dot;
  const resolved = ICONS[key] ? key : ALIAS[key];
  return ICONS[resolved] ?? ICONS.dot;
}

export function Icon({ name, title }: { name?: string | null; title?: string }) {
  const Draw = iconFor(name);
  return <>{Draw({ title })}</>;
}
