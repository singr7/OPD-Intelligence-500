// All doctor-console CSS in one place (built on the doc 04 §1 tokens).
//
// doc 04 §3 for this surface: "dense but calm; summary card is the hero —
// scannable in 20s: red flags as top strip (danger tokens), symptoms as compact
// table, everything else collapsed. Light theme, high contrast, min 14px data
// text." Nothing here goes below 14px, and the only saturated colour on the page
// is the danger stamp and the marigold node of the patient in the room — so both
// mean something the moment they appear.

export const CONSOLE_CSS = `
.console { min-height: 100vh; background: var(--canvas); color: var(--text);
  font-family: var(--font-sans), "Noto Sans", "Noto Sans Devanagari", system-ui, sans-serif; }

/* app bar */
.appbar { position: sticky; top: 0; z-index: 10; min-height: 64px; display: flex; align-items: center;
  justify-content: space-between; gap: 16px; padding: 10px 24px;
  background: var(--shell); border-bottom: 1px solid var(--shell-raised); color: #fff; }
.appbar-l { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.appbar strong { font-size: 17px; color: #fff; }
.appbar .room { font-size: 13px; color: #b9c7c2; }
.appbar-r { display: flex; align-items: center; gap: 10px; }
.appbar kbd.hint { font: 600 12px/1 var(--font-sans), monospace; color: #c5d1cd;
  border: 1px solid #4a5b56; border-bottom-width: 2px; border-radius: 5px;
  padding: 4px 7px; background: var(--shell-raised); }
.appbar-count { font-size: 13px; color: #b9c7c2; }
.appbar-count b { font-size: 16px; color: #fff; font-variant-numeric: tabular-nums; }
.signout { border: none; background: none; color: #b9c7c2; cursor: pointer; font-size: 13px; }

/* ---- the encounter bar (S-UX.6) ---------------------------------------- */
/* One strip, directly above the card: what state this encounter is in, and the
   one thing to press next. It is the only place on the console where a filled
   button appears, so "filled" always means "this is the next step". */
.encounter { max-width: 1100px; margin: 0 auto 14px; display: flex; align-items: center;
  justify-content: space-between; flex-wrap: wrap; gap: 14px; padding: 12px 18px;
  background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--line);
  border-radius: var(--radius-panel); }
.encounter[data-state="called"] { border-left-color: var(--info); }
.encounter[data-state="in_consult"] { border-left-color: var(--brand); }
.encounter[data-state="lab_requeue"] { border-left-color: var(--attention); }
.encounter[data-state="done"], .encounter[data-state="no_show"] { border-left-color: var(--border-strong); }
.enc-who { display: flex; align-items: center; gap: 11px; min-width: 0; }
.enc-dot { flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--border-strong); }
.enc-dot.live { background: var(--brand); box-shadow: 0 0 0 4px var(--brand-soft); }
.enc-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.enc-text strong { font-size: 15px; font-weight: 800; color: var(--ink); letter-spacing: .005em; }
.enc-text span { font-size: 14px; color: var(--ink-soft); }
.enc-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.enc-actions .act { display: inline-flex; align-items: center; gap: 7px; }
.enc-actions .act.quiet { background: none; border-color: transparent; color: var(--ink-soft);
  font-weight: 600; }
.enc-actions .act.quiet:hover { border-color: var(--line); color: var(--ink); }
.enc-actions .act.primary .hint { border-color: rgba(255,255,255,.45); color: #fff;
  background: rgba(255,255,255,.16); }
.enc-actions .hint { font: 700 11px/1 var(--font-sans), monospace; color: var(--text-muted);
  border: 1px solid var(--border); border-bottom-width: 2px; border-radius: 4px;
  padding: 3px 5px; background: var(--canvas); }

.err-toast { margin: 14px 22px 0; background: var(--danger-soft); color: var(--danger);
  border-radius: 12px; padding: 10px 14px; font-weight: 600; font-size: 14px; }
.note-toast { margin: 14px 22px 0; background: var(--accent-soft); color: #7a4d0a;
  border-radius: 12px; padding: 10px 14px; font-weight: 600; font-size: 14px; }
.loading, .empty-state { padding: 60px 22px; text-align: center; color: var(--ink-soft);
  font-size: 15px; }

/* two columns and nothing else */
.split { display: grid; grid-template-columns: minmax(300px, 330px) minmax(0, 1fr); gap: 0;
  max-width: 1480px; margin: 0 auto; min-height: calc(100vh - 64px); align-items: stretch; }

/* ---- the rail: tokens as stations on a spine --------------------------- */
.rail { background: var(--surface); border-right: 1px solid var(--line);
  padding: 20px 14px 8px; position: sticky; top: 64px; height: calc(100vh - 64px);
  overflow-y: auto; }
/* the three scopes. Unassigned is the only one that can raise its voice, and
   it does so in words as well as colour (doc 04 §4 forbids colour-only meaning). */
.scopes { display: flex; gap: 4px; padding: 0 2px 10px; }
.scope { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; align-items: center;
  gap: 2px; padding: 8px 4px; border: 1px solid transparent; border-radius: 9px;
  background: none; cursor: pointer; font: inherit; color: var(--ink-soft); }
.scope:hover { background: var(--bg); }
.scope-name { font-size: 13px; font-weight: 700; letter-spacing: .01em; }
.scope-n { font-size: 17px; font-weight: 800; font-variant-numeric: tabular-nums;
  color: var(--ink); }
.scope.is-open { background: var(--primary-soft); border-color: var(--primary-soft);
  color: var(--primary-d); }
.scope.is-open .scope-n { color: var(--primary-d); }
.scope.is-attention { background: var(--accent-soft); border-color: #e8c583; color: #7a4d0a; }
.scope.is-attention .scope-n { color: #7a4d0a; }
.scope.is-attention.is-open { border-color: var(--accent); }
.unassigned-alert { margin: 0 2px 12px; padding: 7px 11px; border-radius: 9px;
  background: var(--accent-soft); color: #7a4d0a; font-size: 13px; font-weight: 700;
  line-height: 1.4; }

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
.station > button { display: grid; grid-template-columns: 24px 42px 1fr; align-items: start;
  gap: 8px; width: 100%; text-align: left; background: none; border: none; cursor: pointer;
  padding: 10px 8px; border-radius: 7px; font: inherit; color: inherit; }
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
/* whose patient this is — stated only when it is not the reading doctor's */
.station .swho { font-size: 13px; color: var(--ink-soft); margin-top: 2px; }
.station .swho.pool { color: #7a4d0a; font-weight: 700; }
.station .take { margin: 0 8px 8px 74px; border: 1.5px solid var(--line); background: var(--surface);
  color: var(--ink); font: 700 13px/1 var(--font-sans), sans-serif; padding: 8px 12px;
  border-radius: var(--radius-control); cursor: pointer; }
.station .take:hover { border-color: var(--primary); color: var(--primary-d); }
.station .take:disabled { opacity: .5; cursor: default; }

/* ---- the context spine (plan §4.2) ------------------------------------- */
/* Sticky, and it never unmounts. Four things and no fifth: identity + token,
   diagnosis, allergies, red flags. It sits at the top of the stage above the tab
   row, so every tab — including the consult note — is read with the dangerous
   facts still on screen. */
.stage { min-width: 0; padding: 24px; }
.spine-ctx { position: sticky; top: 64px; z-index: 5; max-width: 1100px; margin: 0 auto;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius-panel) var(--radius-panel) 0 0; overflow: hidden; }

.cx-id { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px;
  padding: 16px 22px 0; }
.cx-who { min-width: 0; }
.cx-who h1 { margin: 0; font-size: 24px; line-height: 1.2; color: var(--ink); }
.cx-meta { margin: 5px 0 0; display: flex; flex-wrap: wrap; gap: 9px; font-size: 13px;
  color: var(--ink-soft); align-items: center; }
.cx-mrn { font-variant-numeric: tabular-nums; opacity: .85; }
.cx-state { border-radius: 999px; padding: 2px 8px; background: #eef2f0; color: var(--text-muted);
  font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; }
.cx-state.state-in_consult { background: var(--brand-soft); color: var(--brand-hover); }
.cx-state.state-called { background: var(--info-soft); color: var(--info); }
.cx-state.state-lab_requeue { background: var(--attention-soft); color: #7a4d0a; }
.cx-owner { border-radius: 999px; padding: 2px 9px; background: var(--bg); color: var(--ink);
  font-size: 12px; font-weight: 700; }
.cx-owner.pool { background: var(--accent-soft); color: #7a4d0a; }
/* the token: train-board treatment, tabular numerals — what reconnects this
   console to the board and the coordinator across a corridor */
.cx-tok { flex: none; text-align: right; display: grid; justify-items: end; gap: 2px; }
.cx-tok-n { font-size: 32px; font-weight: 800; color: var(--ink); line-height: 1;
  font-variant-numeric: tabular-nums; letter-spacing: -.01em; }
.cx-tok-l { font-size: 11px; text-transform: uppercase; letter-spacing: .09em;
  color: var(--ink-soft); }

/* one line, never truncated */
.cx-dx { margin: 12px 22px 0; font-size: 16px; font-weight: 700; line-height: 1.4;
  color: var(--ink); }
.cx-dx-src { font-weight: 500; color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.cx-dx-none { font-weight: 500; color: var(--ink-soft); }

.cx-allergy { margin: 6px 22px 0; font-size: 14px; color: var(--ink-soft); line-height: 1.5; }
.cx-allergy-l { font-weight: 800; color: var(--ink); text-transform: uppercase;
  letter-spacing: .06em; font-size: 12px; margin-right: 4px; }

/* No flags is a state, not an absence — it is said plainly, in a calm register,
   so that "nothing fired" and "the strip failed to render" cannot look alike. */
.cx-noflags { margin: 12px 0 0; padding: 9px 22px; background: var(--bg); color: var(--ink-soft);
  font-size: 13px; font-weight: 600; border-top: 1px solid var(--line); }

/* red flags — solid stamps, not pale chips */
.cx-flags { display: flex; flex-direction: column; gap: 1px; background: var(--line);
  margin-top: 12px; }
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

/* ---- the tab row (plan §4.3/§4.4) -------------------------------------- */
/* Four working tabs, and one muted trailing disclosure for the four unbuilt
   surfaces. The disclosure deliberately does not look like a tab: it never
   carries the open underline and it does not hover-highlight, so a placeholder
   is not mistaken for a broken feature. */
.worktabs-wrap { max-width: 1100px; margin: 0 auto; background: var(--surface);
  border: 1px solid var(--line); border-top: 0; }
.worktabs { display: flex; align-items: stretch; gap: 2px; padding: 0 12px; flex-wrap: wrap;
  border-bottom: 1px solid var(--line); }
.wtab { display: inline-flex; align-items: center; gap: 7px; background: none; border: none;
  border-bottom: 3px solid transparent; cursor: pointer; font: 700 14px/1 var(--font-sans), sans-serif;
  color: var(--ink-soft); padding: 13px 12px 11px; }
.wtab:hover { color: var(--ink); }
.wtab.is-open { color: var(--primary-d); border-bottom-color: var(--primary); }
.wtab-n { font-size: 12px; font-weight: 700; color: var(--ink-soft); background: var(--bg);
  border-radius: 999px; padding: 2px 8px; font-variant-numeric: tabular-nums; }
.wtab-signed { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em;
  color: var(--primary-d); background: var(--primary-soft); border-radius: 999px; padding: 3px 8px; }
.wtab-soon { margin-left: auto; background: none; border: none; cursor: pointer;
  font: 600 13px/1 var(--font-sans), sans-serif; color: var(--text-muted); padding: 13px 8px 11px; }
.wtab-soon:hover { color: var(--ink-soft); }
.soon-panel { padding: 12px 22px 14px; background: var(--bg); border-bottom: 1px solid var(--line); }
.soon-panel p { margin: 0 0 6px; font-size: 13px; color: var(--ink-soft); line-height: 1.55; }
.soon-panel strong { color: var(--ink); font-weight: 700; }

/* ---- the work area ----------------------------------------------------- */
/* Unframed sections: headings and whitespace do the grouping, and nothing nests
   (doc 14 principle 5). */
.work { max-width: 1100px; margin: 0 auto; background: var(--surface);
  border: 1px solid var(--line); border-top: 0;
  border-radius: 0 0 var(--radius-panel) var(--radius-panel); padding: 4px 0 20px; }
.work-empty { padding: 34px 22px; color: var(--ink-soft); font-size: 14px; }
/* The consult note is a tab body, not a panel that took the screen: it joins the
   same frame the other three tabs render into. */
.worktabs-wrap + .dict { border-top: 0; border-radius: 0 0 var(--radius-panel) var(--radius-panel); }
.wsec { margin: 18px 22px 0; }
.wsec h2 { margin: 0 0 8px; display: flex; align-items: center; gap: 8px; font-size: 12px;
  font-weight: 800; text-transform: uppercase; letter-spacing: .07em; color: var(--ink-soft);
  border-bottom: 1px solid var(--line); padding-bottom: 6px; }
.wsec-n { font-size: 12px; font-weight: 700; color: var(--ink-soft); background: var(--bg);
  border-radius: 999px; padding: 1px 8px; letter-spacing: 0; }
.lines-note { margin: 0; font-size: 14px; color: var(--ink-soft); line-height: 1.6; }
/* Provenance a doctor can act on, in place of a confidence percentage nobody
   can calibrate (plan §4.3). */
.provenance { margin: 20px 22px 0; padding-top: 12px; border-top: 1px solid var(--line);
  font-size: 13px; color: var(--ink-soft); line-height: 1.5; }
.provenance strong { color: var(--ink); font-weight: 700; }

.concern { margin: 18px 22px 0; font-size: 19px; font-weight: 700; line-height: 1.4;
  color: var(--ink); }
.own-words { margin: 10px 22px 0; padding-left: 14px; border-left: 3px solid var(--primary-soft);
  font-size: 15px; color: var(--ink-soft); line-height: var(--line-indic); }
.own-words .gloss { opacity: .85; font-style: italic; }

.symptoms { width: 100%; border-collapse: collapse; font-size: 14px; }
.symptoms th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: .07em;
  color: var(--ink-soft); font-weight: 700; padding: 0 10px 6px 0;
  border-bottom: 1px solid var(--line); }
.symptoms td { padding: 7px 10px 7px 0; border-bottom: 1px solid var(--line); color: var(--ink); }
.symptoms tr td:first-child { font-weight: 600; }

.unclear { margin: 14px 22px 0; background: var(--accent-soft); color: #7a4d0a;
  border-radius: 10px; padding: 9px 13px; font-size: 14px; font-weight: 600; }

/* actions */
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 18px 22px 4px;
  border-top: 1px solid var(--line); margin-top: 18px; }
.act { border: 1.5px solid var(--line); background: var(--surface); color: var(--ink);
  min-height: 40px; font-size: 14px; font-weight: 700; padding: 0 15px;
  border-radius: var(--radius-control); cursor: pointer; }
.act:hover { border-color: var(--primary); color: var(--primary-d); }
.act.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.act.note-action { margin-left: auto; border-color: var(--brand); color: var(--brand-hover); }
.act.danger-quiet { color: var(--danger); }
.act:disabled { opacity: .5; cursor: default; }

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

@media (max-width: 900px) {
  .split { grid-template-columns: 1fr; }
  .rail { position: relative; top: 0; height: auto; max-height: 420px; border-right: 0;
    border-bottom: 1px solid var(--line); }
  .stage { padding: 16px; }
  /* On one column the rail scrolls away above the stage, so a sticky spine
     would pin itself under nothing. It stays in flow instead. */
  .spine-ctx { position: relative; top: 0; }
}

@media (max-width: 600px) {
  .appbar { padding: 10px 14px; align-items: flex-start; }
  .appbar .room { display: none; }
  .signout { display: none; }
  .stage { padding: 10px; }
  .encounter { align-items: stretch; }
  .enc-actions { width: 100%; }
  .enc-actions .act { flex: 1 1 auto; justify-content: center; }
  .enc-actions .hint { display: none; }
  .cx-id { padding: 14px 16px 0; }
  .cx-who h1 { font-size: 21px; }
  .cx-dx, .cx-allergy { margin-left: 16px; margin-right: 16px; }
  .concern, .own-words, .unclear, .wsec, .provenance { margin-left: 16px; margin-right: 16px; }
  .wtab-soon { margin-left: 0; }
  .timeline li { grid-template-columns: 1fr; gap: 3px; }
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
.dict { max-width: 1100px; margin: 0 auto; background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius-panel); padding: 0 0 20px; overflow: hidden; }

.dict-h { display: flex; align-items: flex-start;
  justify-content: space-between; gap: 16px; padding: 18px 22px 14px;
  border-bottom: 1px solid var(--line); background: var(--surface); }
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

/* The prescription is the signed note's primary output, not a quiet receipt. */
.rx { margin: 18px 22px 4px; border: 1px solid var(--line-strong);
  border-radius: var(--radius-panel); background: #fff; overflow: hidden;
  scroll-margin-top: 148px; }
.rx:focus { outline: 2px solid var(--brand); outline-offset: 3px; }
.rx-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px;
  padding: 20px; background: var(--surface-subtle); border-bottom: 1px solid var(--line); }
.rx-kicker { display: block; margin-bottom: 4px; color: var(--brand); font-size: 11px;
  font-weight: 900; text-transform: uppercase; letter-spacing: .08em; }
.rx-head h3 { margin: 0; color: var(--text); font-size: 24px; line-height: 1.2; }
.rx-head p { margin: 5px 0 0; color: var(--text-muted); font-size: 13px; }
.rx-prescriber { display: grid; justify-items: end; gap: 2px; text-align: right; }
.rx-prescriber span { color: var(--text-faint); font-size: 11px; text-transform: uppercase;
  font-weight: 800; letter-spacing: .06em; }
.rx-prescriber strong { font-size: 14px; }
.rx-prescriber small { color: var(--text-muted); font-size: 12px; }

.rx-columns, .rx-row { display: grid;
  grid-template-columns: minmax(180px, 1.45fr) minmax(130px, 1fr) minmax(145px, 1fr) 90px minmax(130px, .9fr);
  gap: 14px; align-items: center; }
.rx-columns { padding: 9px 20px; background: var(--shell); color: #dce5e2;
  font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }
.rx-list { list-style: none; margin: 0; padding: 0 20px; }
.rx-row { padding: 15px 0; border-bottom: 1px solid var(--line); }
.rx-row:last-child { border-bottom: 0; }
.rx-row.is-flagged { margin: 0 -20px; padding-left: 17px; padding-right: 20px;
  border-left: 3px solid var(--danger); background: var(--danger-soft); }
.rx-name { min-width: 0; color: var(--text); font-size: 16px; font-weight: 800; overflow-wrap: anywhere; }
.rx-row.is-flagged .rx-name { color: var(--danger); }
.rx-dose { color: var(--text); font-size: 14px; font-weight: 650; }
.rx-when { display: flex; align-items: center; gap: 10px; min-width: 0; }
.rx-dur { color: var(--text); font-size: 14px; }
.rx-safety { color: var(--success); font-size: 12px; font-weight: 800; }
.rx-safety.is-flagged { color: var(--danger); }
.rx-mobile-label { display: none; }

.rx-slots { display: inline-flex; gap: 6px; }
.rx-slots i { font-style: normal; font-size: 19px; line-height: 1; color: var(--line-strong); }
.rx-slots i.on { color: var(--attention); }
.rx-slots i.night.on { color: var(--info); }
.rx-count-only, .rx-words { color: var(--text); font-size: 14px; font-weight: 700; }
.rx-words { border-bottom: 1px dashed var(--line-strong); overflow-wrap: anywhere; }
.rx-why { grid-column: 1 / -1; display: flex; align-items: flex-start; gap: 7px;
  color: var(--danger); font-size: 13px; font-weight: 700; line-height: 1.45; }
.rx-why svg, .rx-flagnote svg { width: 16px; height: 16px; flex: none; margin-top: 2px; }

.rx-flagnote { display: flex; align-items: flex-start; gap: 8px; margin: 0;
  padding: 12px 20px; border-top: 1px solid #f0caca; background: #fff7f7;
  color: var(--danger); font-size: 13px; font-weight: 650; line-height: 1.5; }
.rx-actions { display: flex; gap: 10px; flex-wrap: wrap; padding: 18px 20px 0; }
.rx-copy-choice { flex-basis: 100%; display: inline-flex; gap: 0; }
.rx-copy-choice button { min-height: 36px; border: 1px solid var(--line-strong);
  background: #fff; color: var(--text-muted); padding: 0 13px; font: inherit;
  font-size: 12px; font-weight: 800; cursor: pointer; }
.rx-copy-choice button:first-child { border-radius: var(--radius-control) 0 0 var(--radius-control); }
.rx-copy-choice button:last-child { margin-left: -1px;
  border-radius: 0 var(--radius-control) var(--radius-control) 0; }
.rx-copy-choice button[aria-pressed="true"] { position: relative; border-color: var(--brand);
  background: var(--brand-soft); color: var(--brand-hover); }
.rx-print, .rx-send button { min-height: 40px; display: inline-flex; align-items: center;
  justify-content: center; gap: 8px; border-radius: var(--radius-control); cursor: pointer; }
.rx-print { border: 1px solid var(--brand); background: #fff; color: var(--brand-hover);
  font: inherit; font-size: 14px; font-weight: 800; padding: 0 17px; }
.rx-print svg, .rx-send button svg { width: 17px; height: 17px; }
.rx-print.is-patient { background: var(--brand); border-color: var(--brand); color: #fff; }
.rx-print:disabled { border-color: var(--line); background: var(--line); color: var(--text-muted);
  cursor: default; }
.rx-send { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 12px 20px 20px; }
.rx-send button { border: 1px solid var(--line); background: #fff; color: var(--text);
  font: inherit; font-size: 13px; font-weight: 700; padding: 0 14px; }
.rx-send button:disabled { color: var(--text-muted); cursor: default; }
.rx-deliv { font-size: 12px; font-weight: 800; color: var(--brand-hover); }
.rx-deliv.is-failed { color: var(--danger); }
.rx-err { margin: 0; padding: 0 20px 18px; font-size: 13px; font-weight: 700; color: var(--danger); }

@media (max-width: 980px) {
  .rx-columns { display: none; }
  .rx-row { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; }
  .rx-mobile-label { display: block; margin-bottom: 3px; color: var(--text-faint);
    font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; }
  .rx-why { grid-column: 1 / -1; }
}

@media (max-width: 600px) {
  .dict-h { position: static; padding: 16px; }
  .dict-review, .dict-capture { padding-left: 16px; padding-right: 16px; }
  .rx { margin: 14px 10px 0; }
  .rx-head { flex-direction: column; padding: 16px; }
  .rx-prescriber { justify-items: start; text-align: left; }
  .rx-row { grid-template-columns: 1fr; gap: 11px; }
  .rx-row.is-flagged { margin: 0 -20px; }
  .rx-why { grid-column: 1; }
  .rx-actions, .rx-send { padding-left: 16px; padding-right: 16px; }
  .rx-print { width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .dict-mic.is-rec .dict-dot { animation: none; }
}
`;
