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

// ---- the dictation panel (S10, doc 03 §7) ---------------------------------
//
// One idea carries this surface: the **provenance line**. Every written value
// sits above the doctor's own words, joined by a hairline down the left. When
// the two agree the hairline is grey and the eye slides past; when they cannot
// be reconciled it turns danger-red and the row steps out of alignment, so a
// renamed drug is visibly out of line with the rest of the note before it is
// even read.
//
// No cards, no shadows, no third colour. Danger and marigold already mean
// something exact in this console and they keep meaning it here: red is "this
// could hurt someone", marigold is "you have seen it and it still stands".

export const DICTATION_CSS = `
.dict { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 0 0 20px; overflow: hidden; }

.dict-h { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;
  padding: 20px 22px 14px; border-bottom: 1px solid var(--line); }
.dict-h h2 { margin: 0; font-size: 21px; line-height: 1.2; color: var(--ink); }
.dict-sub { margin: 5px 0 0; font-size: 14px; color: var(--ink-soft); }
.dict-model { opacity: .75; }
.dict-close { border: none; background: none; color: var(--ink-soft); font-size: 14px;
  cursor: pointer; padding: 4px 2px; }

.dict-err { margin: 14px 22px 0; background: var(--danger-soft); color: var(--danger);
  border-radius: 12px; padding: 10px 14px; font-size: 14px; font-weight: 600; line-height: 1.5; }
.dict-signed { margin: 14px 22px 0; background: var(--primary-soft); color: var(--primary-d);
  border-radius: 12px; padding: 10px 14px; font-size: 14px; font-weight: 700; }

/* capture — loud before the note exists, a quiet strip afterwards */
.dict-capture { padding: 16px 22px 0; }
.dict-caprow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.dict-mic { display: inline-flex; align-items: center; gap: 9px; border: 1.5px solid var(--line);
  background: var(--surface); color: var(--ink); font: inherit; font-size: 15px; font-weight: 700;
  padding: 11px 18px; border-radius: 12px; cursor: pointer; }
.dict-mic .dict-dot { width: 11px; height: 11px; border-radius: 50%; background: var(--ink-soft); }
.dict-mic.is-rec { border-color: var(--danger); color: var(--danger); }
.dict-mic.is-rec .dict-dot { background: var(--danger); animation: dictpulse 1.4s var(--ease) infinite; }
@keyframes dictpulse { 0%,100% { opacity: 1; transform: scale(1); }
  50% { opacity: .45; transform: scale(1.35); } }
.dict-map { border: none; background: var(--primary); color: #fff; font: inherit; font-size: 15px;
  font-weight: 700; padding: 12px 20px; border-radius: 12px; cursor: pointer; }
.dict-map:disabled, .dict-mic:disabled { opacity: .5; cursor: default; }
.dict-busy { font-size: 14px; color: var(--ink-soft); }
.dict-transcript { display: block; width: 100%; margin-top: 12px; padding: 12px 14px;
  border: 1px solid var(--line); border-radius: 12px; background: var(--bg); color: var(--ink);
  font: inherit; font-size: 15px; line-height: 1.6; resize: vertical; }
.dict-capture.is-done .dict-transcript { font-size: 14px; color: var(--ink-soft); }

/* review */
.dict-review { padding: 4px 22px 0; }
.dict-review h3 { margin: 22px 0 10px; font-size: 12px; text-transform: uppercase;
  letter-spacing: .09em; color: var(--ink-soft); font-weight: 800; }
.dict-flagged h3 { color: var(--danger); }
.dict-nomeds { font-size: 14px; color: var(--ink-soft); margin: 18px 0 0; }

/* one drug */
.med { padding: 12px 0 12px 14px; border-left: 2px solid var(--line); margin-bottom: 4px; }
.med-line { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.med-name { border: none; background: none; padding: 0; font: inherit; font-size: 17px;
  font-weight: 800; color: var(--ink); cursor: pointer; text-align: left;
  border-bottom: 1px dashed transparent; }
.med-name:hover:not(:disabled) { border-bottom-color: var(--ink-soft); }
.med-name:disabled { cursor: default; }
.med-input { font: inherit; font-size: 17px; font-weight: 800; color: var(--ink); padding: 3px 8px;
  border: 1.5px solid var(--primary); border-radius: 8px; background: var(--surface);
  min-width: 220px; }
.med-sig { font-size: 14px; color: var(--ink); font-variant-numeric: tabular-nums; }
.med-generic { font-size: 13px; color: var(--ink-soft); font-style: italic; }

/* the provenance line: what was said, hanging under what was written */
.med-spoken { display: flex; align-items: flex-start; gap: 8px; margin-top: 5px; }
.med-tick { flex: none; width: 10px; height: 10px; margin-top: 6px;
  border-left: 1px solid var(--line); border-bottom: 1px solid var(--line);
  border-bottom-left-radius: 3px; }
.med-heard { font-size: 14px; color: var(--ink-soft); line-height: var(--line-indic); }

/* flagged: the hairline goes red and the row steps out of line */
.med-flag { border-left-color: var(--danger); background: var(--danger-soft);
  border-radius: 0 12px 12px 0; margin-left: -6px; padding-left: 20px; padding-right: 14px; }
.med-flag .med-tick { border-color: var(--danger); }
.med-flag .med-heard { color: var(--danger); }

/* acknowledged: calms to marigold — seen and standing, not resolved */
.med-ack { border-left-color: var(--accent); background: var(--accent-soft);
  border-radius: 0 12px 12px 0; margin-left: -6px; padding-left: 20px; padding-right: 14px; }
.med-ack .med-tick { border-color: var(--accent); }

.med-why { margin-top: 9px; }
.med-alert { margin: 0 0 6px; font-size: 14px; line-height: 1.55; color: var(--danger); }
.med-alert strong { font-weight: 800; }
.med-sugg { margin: 0 0 8px; font-size: 14px; color: var(--ink-soft); }
.med-cand { display: inline-block; margin-right: 10px; font-weight: 700; color: var(--ink); }
.med-cand em { font-weight: 500; font-style: italic; color: var(--ink-soft); }
.med-confirm { border: 1.5px solid var(--danger); background: var(--surface); color: var(--danger);
  font: inherit; font-size: 14px; font-weight: 700; padding: 9px 15px; border-radius: 11px;
  cursor: pointer; }
.med-acked { margin: 0; font-size: 13px; font-weight: 700; color: #7a4d0a; }

/* the quieter fields, same provenance idea at lower volume */
.prov { display: grid; grid-template-columns: 92px 1fr; gap: 14px; align-items: baseline;
  padding: 11px 0; border-top: 1px solid var(--line); }
.prov-label { font-size: 12px; text-transform: uppercase; letter-spacing: .07em;
  color: var(--ink-soft); font-weight: 700; }
.prov-body { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.prov-written { font-size: 15px; font-weight: 600; color: var(--ink); line-height: 1.45; }
.prov-spoken { font-size: 14px; color: var(--ink-soft); line-height: var(--line-indic); }

.dict-advice { padding-top: 4px; }
.dict-advice ul { margin: 0; padding-left: 20px; }
.dict-advice li { font-size: 14px; line-height: 1.6; color: var(--ink); margin-bottom: 5px; }
.dict-unclear { margin: 14px 0 0; background: var(--accent-soft); color: #7a4d0a;
  border-radius: 10px; padding: 9px 13px; font-size: 14px; font-weight: 600; line-height: 1.5; }

.dict-signbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--line); }
.dict-sign { border: none; background: var(--primary); color: #fff; font: inherit; font-size: 16px;
  font-weight: 800; padding: 14px 26px; border-radius: 12px; cursor: pointer; }
.dict-sign:disabled { background: var(--line); color: var(--ink-soft); cursor: default; }
.dict-block { margin: 0; font-size: 14px; font-weight: 600; color: var(--danger); line-height: 1.5; }

/* prescription (S11, doc 03 §8) — the consequence of the signature, in the same
   column. Deliberately quiet: nothing here changes a dose, so it reads as a
   receipt rather than a second form. The one loud element is the flag, which is
   the only thing on the panel a human still has to act on. */
.rx { margin: 18px 22px 0; border-top: 1px solid var(--line); padding-top: 16px; }
.rx-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.rx-head h3 { margin: 0; font-size: 13px; font-weight: 800; letter-spacing: .07em;
  text-transform: uppercase; color: var(--ink-soft); }
.rx-count { font-size: 13px; color: var(--ink-soft); }

.rx-list { list-style: none; margin: 12px 0 0; padding: 0; }
.rx-row { display: grid; grid-template-columns: 1fr auto; gap: 6px 14px;
  align-items: baseline; padding: 9px 0; border-bottom: 1px solid var(--line); }
.rx-row.is-flagged { border-left: 3px solid var(--danger); padding-left: 11px;
  background: var(--danger-soft); }
.rx-name { font-size: 16px; font-weight: 700; color: var(--ink); }
.rx-row.is-flagged .rx-name { color: var(--danger); }
.rx-dose { font-weight: 500; color: var(--ink-soft); }
.rx-when { display: flex; align-items: center; gap: 12px; justify-self: end; }
.rx-dur { font-size: 13px; color: var(--ink-soft); }

/* Icons mirror the printed sheet exactly — same slots_known rule, so what the
   doctor previews is what the patient is handed. */
.rx-slots { display: inline-flex; gap: 7px; }
.rx-slots i { font-style: normal; font-size: 19px; line-height: 1; color: var(--line); }
.rx-slots i.on { color: var(--accent); }
.rx-slots i.night.on { color: var(--primary-d); }
/* A stated count with no stated time of day. Never drawn as icons. */
.rx-count-only { font-size: 14px; font-weight: 700; color: var(--ink); }
/* Not readable as a schedule: the doctor's own words, verbatim. */
.rx-words { font-size: 14px; color: var(--ink); border-bottom: 1px dashed var(--line); }
.rx-why { grid-column: 1 / -1; font-size: 13px; font-weight: 600; color: var(--danger); }

.rx-flagnote { margin: 12px 0 0; font-size: 14px; font-weight: 600; color: var(--danger);
  line-height: 1.5; }

.rx-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
.rx-print { border: 1.5px solid var(--primary); background: #fff; color: var(--primary-d);
  font: inherit; font-size: 15px; font-weight: 700; padding: 11px 20px; border-radius: 11px;
  cursor: pointer; }
.rx-print.is-patient { background: var(--primary); border-color: var(--primary); color: #fff; }
.rx-print:disabled { border-color: var(--line); background: var(--line); color: var(--ink-soft);
  cursor: default; }

.rx-send { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
.rx-send button { border: 1px solid var(--line); background: #fff; color: var(--ink);
  font: inherit; font-size: 14px; padding: 8px 16px; border-radius: 999px; cursor: pointer; }
.rx-send button:disabled { color: var(--ink-soft); cursor: default; }
.rx-deliv { font-size: 13px; font-weight: 700; color: var(--primary-d); }
.rx-deliv.is-failed { color: var(--danger); }
.rx-err { margin: 10px 0 0; font-size: 14px; font-weight: 600; color: var(--danger); }

@media (prefers-reduced-motion: reduce) {
  .dict-mic.is-rec .dict-dot { animation: none; }
}
`;
