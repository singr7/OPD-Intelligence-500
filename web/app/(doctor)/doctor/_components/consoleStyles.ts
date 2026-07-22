// All doctor-console CSS in one place (built on the doc 04 §1 tokens).
//
// doc 04 §3 for this surface: "dense but calm; summary card is the hero —
// scannable in 20s: red flags as top strip (danger tokens), symptoms as compact
// table, everything else collapsed. Light theme, high contrast, min 14px data
// text." Nothing here goes below 14px, and the only saturated colour on the page
// is the danger stamp and the marigold node of the patient in the room — so both
// mean something the moment they appear.

export const CONSOLE_CSS = `
.console { min-height: 100vh; background: var(--bg); color: var(--ink);
  font-family: var(--font-sans), "Noto Sans", "Noto Sans Devanagari", system-ui, sans-serif; }

/* app bar */
.appbar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center;
  justify-content: space-between; gap: 16px; padding: 12px 22px;
  background: var(--surface); border-bottom: 1px solid var(--line); }
.appbar-l { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.appbar strong { font-size: 17px; color: var(--ink); }
.appbar .room { font-size: 14px; color: var(--ink-soft); }
.appbar-r { display: flex; align-items: center; gap: 10px; }
.appbar kbd.hint { font: 600 12px/1 var(--font-sans), monospace; color: var(--ink-soft);
  border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 6px;
  padding: 4px 7px; background: var(--bg); }
.callnext { border: none; background: var(--primary); color: #fff; font-weight: 700;
  font-size: 15px; padding: 11px 20px; border-radius: 12px; cursor: pointer; }
.callnext:disabled { opacity: .55; cursor: default; }
.signout { border: none; background: none; color: var(--ink-soft); cursor: pointer; font-size: 14px; }

.err-toast { margin: 14px 22px 0; background: var(--danger-soft); color: var(--danger);
  border-radius: 12px; padding: 10px 14px; font-weight: 600; font-size: 14px; }
.note-toast { margin: 14px 22px 0; background: var(--accent-soft); color: #7a4d0a;
  border-radius: 12px; padding: 10px 14px; font-weight: 600; font-size: 14px; }
.loading, .empty-state { padding: 60px 22px; text-align: center; color: var(--ink-soft);
  font-size: 15px; }

/* two columns and nothing else */
.split { display: grid; grid-template-columns: minmax(300px, 360px) 1fr; gap: 22px;
  padding: 22px; align-items: start; }
@media (max-width: 900px) { .split { grid-template-columns: 1fr; } }

/* ---- the rail: tokens as stations on a spine --------------------------- */
.rail { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 18px 16px 8px; position: sticky; top: 78px; }
.rail-h { display: flex; align-items: baseline; gap: 8px; padding: 0 6px 12px; }
.rail-count { font-size: 26px; font-weight: 800; color: var(--ink);
  font-variant-numeric: tabular-nums; }
.rail-label { font-size: 14px; color: var(--ink-soft); }
.rail-empty { font-size: 14px; color: var(--ink-soft); line-height: 1.55; padding: 4px 6px 18px; }

.spine { list-style: none; margin: 0; padding: 0 0 12px; position: relative; }
/* the rail itself */
.spine::before { content: ""; position: absolute; left: 21px; top: 12px; bottom: 20px;
  width: 2px; background: var(--line); border-radius: 2px; }

.station { position: relative; }
.station > button { display: grid; grid-template-columns: 30px 44px 1fr; align-items: start;
  gap: 8px; width: 100%; text-align: left; background: none; border: none; cursor: pointer;
  padding: 10px 8px; border-radius: 14px; font: inherit; color: inherit; }
.station > button:hover { background: var(--bg); }
.station.is-selected > button { background: var(--primary-soft); }

.station .node { grid-column: 1; margin: 4px auto 0; width: 13px; height: 13px; border-radius: 50%;
  background: var(--surface); border: 2px solid var(--line); box-shadow: 0 0 0 4px var(--surface);
  position: relative; z-index: 1; }
.station.is-selected .node { box-shadow: 0 0 0 4px var(--primary-soft); }
/* the patient in the room: the one filled marigold node on the page */
.station.is-active .node { background: var(--accent); border-color: var(--accent); }
.station.urgent .node { border-color: var(--danger); }
.station.semi .node { border-color: var(--accent); }
.station.lab_requeue .node { border-style: dashed; }

.station .stok { grid-column: 2; font-size: 19px; font-weight: 800; color: var(--ink);
  font-variant-numeric: tabular-nums; letter-spacing: -.01em; }
.station.urgent .stok { color: var(--danger); }
.station .sbody { grid-column: 3; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.station .sname { font-size: 15px; font-weight: 700; color: var(--ink); }
.station .sname em { font-style: normal; font-weight: 500; color: var(--ink-soft); }
.station .scc { font-size: 14px; color: var(--ink-soft); line-height: var(--line-indic);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.station .sfoot { display: flex; gap: 8px; align-items: center; margin-top: 2px; }
.station .sstate { font-size: 13px; font-weight: 600; color: var(--ink-soft);
  text-transform: uppercase; letter-spacing: .05em; }
.station.is-active .sstate { color: #7a4d0a; }
.station .sflag { font-size: 13px; font-weight: 700; color: var(--danger); }
.station .sreason { font-size: 13px; color: var(--danger); line-height: 1.45; margin-top: 2px; }

/* ---- the card ---------------------------------------------------------- */
.stage { min-width: 0; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 0 0 18px; overflow: hidden; }

/* 1. red flags — solid stamps, not pale chips */
.flagstrip { display: flex; flex-direction: column; gap: 1px; background: var(--line); }
.stamp { display: flex; align-items: flex-start; gap: 12px; padding: 13px 22px;
  background: var(--danger); color: #fff; }
.stamp.semi { background: #8a5a10; }
.stamp-mark { flex: none; width: 22px; height: 22px; border-radius: 50%; background: rgba(255,255,255,.22);
  display: grid; place-items: center; font-weight: 900; font-size: 14px; margin-top: 1px; }
.stamp-body { display: flex; flex-direction: column; gap: 2px; }
.stamp-body strong { font-size: 16px; font-weight: 800; letter-spacing: .005em; }
.stamp-body em { font-style: normal; font-size: 14px; opacity: .92; line-height: 1.5; }
.stamp-said { font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
  font-size: 11px; opacity: .8; }

/* 2. who, concern, symptoms */
.who { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px;
  padding: 20px 22px 0; }
.who h1 { margin: 0; font-size: 26px; line-height: 1.2; color: var(--ink); }
.who .meta { margin: 6px 0 0; display: flex; flex-wrap: wrap; gap: 10px; font-size: 14px;
  color: var(--ink-soft); }
.who .meta .mrn { font-variant-numeric: tabular-nums; opacity: .8; }
.who-r { text-align: right; flex: none; }
.who-r .tok { display: block; font-size: 34px; font-weight: 800; color: var(--ink);
  font-variant-numeric: tabular-nums; line-height: 1; }
.who-r .tok-label { font-size: 12px; text-transform: uppercase; letter-spacing: .09em;
  color: var(--ink-soft); }

.concern { margin: 14px 22px 0; font-size: 19px; font-weight: 700; line-height: 1.4;
  color: var(--ink); }
.own-words { margin: 10px 22px 0; padding-left: 14px; border-left: 3px solid var(--primary-soft);
  font-size: 15px; color: var(--ink-soft); line-height: var(--line-indic); }
.own-words .gloss { opacity: .85; font-style: italic; }

.symptoms { width: calc(100% - 44px); margin: 16px 22px 0; border-collapse: collapse;
  font-size: 14px; }
.symptoms th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: .07em;
  color: var(--ink-soft); font-weight: 700; padding: 0 10px 6px 0;
  border-bottom: 1px solid var(--line); }
.symptoms td { padding: 7px 10px 7px 0; border-bottom: 1px solid var(--line); color: var(--ink); }
.symptoms tr td:first-child { font-weight: 600; }

.unclear { margin: 14px 22px 0; background: var(--accent-soft); color: #7a4d0a;
  border-radius: 10px; padding: 9px 13px; font-size: 14px; font-weight: 600; }

/* actions */
.actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 18px 22px 4px; }
.act { border: 1.5px solid var(--line); background: var(--surface); color: var(--ink);
  font-size: 14px; font-weight: 600; padding: 10px 15px; border-radius: 11px; cursor: pointer; }
.act:hover { border-color: var(--primary); color: var(--primary-d); }
.act.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.act:disabled { opacity: .5; cursor: default; }

/* 3. everything else, collapsed */
.fold { margin: 14px 22px 0; border-top: 1px solid var(--line); }
.fold-h { display: flex; align-items: center; gap: 9px; width: 100%; background: none; border: none;
  cursor: pointer; padding: 12px 0 10px; font: inherit; font-size: 14px; font-weight: 700;
  color: var(--ink); text-align: left; }
.fold-h .chev { display: inline-block; color: var(--ink-soft); font-size: 17px; line-height: 1;
  transition: transform var(--dur) var(--ease); }
.fold.open .fold-h .chev { transform: rotate(90deg); }
.fold-n { margin-left: auto; font-size: 13px; font-weight: 700; color: var(--ink-soft);
  background: var(--bg); border-radius: 999px; padding: 2px 9px; }
.fold-b { padding: 0 0 14px; }

.lines { margin: 0; padding-left: 20px; }
.lines li { font-size: 14px; line-height: 1.6; color: var(--ink); margin-bottom: 5px; }

.answers { list-style: none; margin: 0; padding: 0; }
.answers li { display: grid; grid-template-columns: 1fr auto; gap: 10px 16px; align-items: baseline;
  padding: 8px 10px; border-radius: 9px; }
.answers li:nth-child(odd) { background: var(--bg); }
.answers li.flagged { background: var(--danger-soft); }
.answers .q { font-size: 14px; color: var(--ink-soft); line-height: 1.5; }
.answers .a { font-size: 14px; font-weight: 700; color: var(--ink); text-align: right; }
.answers .said { font-weight: 500; font-style: normal; color: var(--ink-soft);
  line-height: var(--line-indic); }

.trends { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.trends li { display: grid; grid-template-columns: 92px auto 1fr; gap: 12px; align-items: center; }
.trends .tname { font-size: 14px; font-weight: 600; color: var(--ink); text-transform: capitalize; }
.trends .spark { display: block; }
.trends .tdelta { font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }
.trends .tdelta.up { color: var(--danger); }
.trends .tdelta.down { color: var(--primary); }

.timeline { list-style: none; margin: 0; padding: 0; }
.timeline li { display: grid; grid-template-columns: 96px 130px 1fr auto; gap: 12px;
  align-items: baseline; padding: 8px 10px; border-radius: 9px; font-size: 14px; }
.timeline li:nth-child(even) { background: var(--bg); }
.timeline li.now { background: var(--primary-soft); font-weight: 600; }
.timeline .tdate { font-variant-numeric: tabular-nums; color: var(--ink-soft); }
.timeline .tdept { color: var(--ink); }
.timeline .tcc { color: var(--ink-soft); line-height: var(--line-indic); }
.timeline .tstatus { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--ink-soft); }

@media (prefers-reduced-motion: reduce) {
  .fold-h .chev { transition: none; }
}
`;
