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
.scope.is-open, .scope.is-attention.is-open { box-shadow: inset 0 -3px 0 currentColor; }
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
.station > .srow { display: grid; grid-template-columns: 24px 42px 1fr; align-items: start;
  gap: 8px; width: 100%; text-align: left; background: none; border: none; cursor: pointer;
  padding: 10px 8px; border-radius: 7px; font: inherit; color: inherit; }
.station > .srow:hover { background: var(--bg); }
.station.is-selected > .srow { background: var(--primary-soft); }

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
.station .take { display: block; margin: 0 8px 10px 74px; white-space: nowrap;
  border: 1.5px solid var(--line); background: var(--surface); color: var(--ink);
  font: 700 13px/1 var(--font-sans), sans-serif; padding: 9px 12px;
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

/* the four steps, stated rather than implied — an indicator, not navigation */
.dict-steps { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 18px;
  list-style: none; margin: 0; padding: 12px 22px; border-bottom: 1px solid var(--line);
  background: var(--surface-subtle); }
.dstep { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; }
.dstep-n { display: grid; place-items: center; width: 21px; height: 21px; flex: none;
  border-radius: 50%; border: 1.5px solid var(--line-strong); color: var(--ink-soft);
  font-size: 12px; font-weight: 800; font-variant-numeric: tabular-nums; }
.dstep-l { color: var(--ink-soft); font-weight: 650; }
.dstep-l em { font-style: normal; font-weight: 500; opacity: .8; }
.dstep.is-now .dstep-n { border-color: var(--primary); background: var(--primary); color: #fff; }
.dstep.is-now .dstep-l { color: var(--ink); font-weight: 800; }
.dstep.is-done .dstep-n { border-color: var(--primary); color: var(--primary-d); }
.dstep.is-done .dstep-l { color: var(--ink); }

/* capture — loud before the note exists, a quiet strip afterwards */
.dict-capture { padding: 16px 22px 0; }
.dict-caprow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

/* Dictate is the primary. Stopping in order to transcribe is safe expected
   progress, so it stays green — the red belongs to the indicator dot, which is
   a status, not the forward action. */
.dict-dictate { display: inline-flex; align-items: center; gap: 9px; min-height: 44px;
  border: none; background: var(--primary); color: #fff; font: inherit; font-size: 15px;
  font-weight: 800; padding: 11px 20px; border-radius: 12px; cursor: pointer; }
.dict-dictate .dict-dot { width: 11px; height: 11px; border-radius: 50%; background: #fff; opacity: .8; }
.dict-dictate.is-rec .dict-dot { background: var(--danger); opacity: 1;
  box-shadow: 0 0 0 3px rgba(255,255,255,.65); animation: dictpulse 1.4s var(--ease) infinite; }
@keyframes dictpulse { 0%,100% { opacity: 1; transform: scale(1); }
  50% { opacity: .45; transform: scale(1.35); } }

/* Typing is equally legitimate and plainly styled — not a lesser path. */
.dict-type, .dict-map { min-height: 44px; border: 1.5px solid var(--line-strong);
  background: var(--surface); color: var(--ink); font: inherit; font-size: 15px;
  font-weight: 700; padding: 11px 18px; border-radius: 12px; cursor: pointer; }
.dict-type:disabled, .dict-map:disabled, .dict-dictate:disabled { opacity: .5; cursor: default; }

/* the escape hatch: reachable, never the loudest thing on the screen */
.dict-more { position: relative; margin-left: auto; }
.dict-more-btn { display: inline-flex; align-items: center; gap: 5px; min-height: 40px;
  border: none; background: none; color: var(--ink-soft); font: inherit; font-size: 14px;
  font-weight: 650; padding: 8px 10px; border-radius: 10px; cursor: pointer; }
.dict-more-btn:hover { background: var(--bg); color: var(--ink); }
.dict-more-btn svg { width: 15px; height: 15px; }
.dict-menu { position: absolute; right: 0; top: calc(100% + 4px); z-index: 20; width: 290px;
  background: var(--surface); border: 1px solid var(--line-strong);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-popover); padding: 6px; }
.dict-menu button { display: block; width: 100%; text-align: left; border: none; background: none;
  font: inherit; font-size: 14px; font-weight: 700; color: var(--ink); padding: 10px 12px;
  border-radius: 8px; cursor: pointer; }
.dict-menu button:hover { background: var(--bg); }
.dict-menu small { display: block; margin-top: 3px; font-size: 12.5px; font-weight: 500;
  color: var(--ink-soft); line-height: 1.45; }

/* Recording: an elapsed time that is simply true, and bars that are real
   samples off the analyser. No analyser, no bars — an evenly spaced pattern
   would be a claim that audio is being captured. */
.dict-rec { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.dict-rec-live { display: inline-flex; align-items: center; gap: 7px; font-size: 13px;
  font-weight: 800; color: var(--danger); text-transform: uppercase; letter-spacing: .07em; }
.dict-rec-live::before { content: ""; width: 9px; height: 9px; border-radius: 50%;
  background: var(--danger); animation: dictpulse 1.4s var(--ease) infinite; }
.dict-rec-time { font-size: 15px; font-weight: 700; color: var(--ink);
  font-variant-numeric: tabular-nums; }
.dict-meter { display: flex; align-items: flex-end; gap: 2px; height: 26px; flex: 1; min-width: 0; }
.dict-meter i { flex: 1; min-width: 2px; max-width: 6px; background: var(--primary);
  border-radius: 2px; opacity: .85; }
.dict-meter-off { font-size: 13px; color: var(--ink-soft); }

/* a mapping failure the doctor can walk out of */
.dict-recover { margin: 14px 22px 0; background: var(--accent-soft); color: #7a4d0a;
  border-left: 3px solid var(--accent); border-radius: 0 10px 10px 0; padding: 11px 14px;
  font-size: 14px; line-height: 1.55; }
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

/* The signature line, editable. The medicine name stays the strongest text on
   the row (doc 14 §7.2) — these are quiet fields under it, not competitors. */
.med-sig-edit { display: inline-flex; flex-wrap: wrap; gap: 6px; }
.med-sigin { width: 92px; min-height: 32px; padding: 5px 8px; font: inherit; font-size: 13px;
  color: var(--ink); background: var(--surface); border: 1px solid var(--line);
  border-radius: 7px; font-variant-numeric: tabular-nums; }
.med-sigin:focus { outline: none; border-color: var(--primary); box-shadow: var(--shadow-focus); }

/* Removing a line is the one edit that cannot be seen afterwards by reading
   the note, so it is named, tooltipped and confirmed. */
.med-del { display: inline-grid; place-items: center; width: 32px; height: 32px; flex: none;
  border: none; background: none; color: var(--ink-soft); border-radius: 8px; cursor: pointer; }
.med-del:hover { background: var(--danger-soft); color: var(--danger); }
.med-del svg { width: 16px; height: 16px; }
.med-delconfirm { display: inline-flex; align-items: center; gap: 8px; font-size: 13px;
  font-weight: 700; color: var(--danger); }
.med-delconfirm button { border: 1.5px solid var(--danger); background: var(--surface);
  color: var(--danger); font: inherit; font-size: 13px; font-weight: 700; min-height: 32px;
  padding: 4px 12px; border-radius: 8px; cursor: pointer; }
.med-delconfirm button:last-child { border-color: var(--line-strong); color: var(--ink); }

/* Adding a line by hand — what "Type note" needs in order to mean anything. */
.med-add { display: inline-flex; align-items: center; gap: 7px; min-height: 40px; margin-top: 12px;
  border: 1.5px dashed var(--line-strong); background: none; color: var(--ink); font: inherit;
  font-size: 14px; font-weight: 700; padding: 9px 16px; border-radius: 11px; cursor: pointer; }
.med-add:hover { border-color: var(--primary); color: var(--primary-d); }
.med-add svg { width: 16px; height: 16px; }
.med-addform { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px;
  padding: 12px; background: var(--bg); border: 1px solid var(--line); border-radius: 12px; }
.med-addform input { min-height: 38px; padding: 8px 10px; font: inherit; font-size: 14px;
  border: 1px solid var(--line-strong); border-radius: 8px; background: var(--surface);
  color: var(--ink); width: 118px; }
.med-addform input:first-child { width: 230px; font-weight: 700; }
.med-addform button { min-height: 38px; border: none; background: var(--primary); color: #fff;
  font: inherit; font-size: 14px; font-weight: 700; padding: 8px 18px; border-radius: 9px;
  cursor: pointer; }
.med-addform button:disabled { opacity: .5; cursor: default; }
.med-addform button.is-quiet { background: none; color: var(--ink-soft); font-weight: 650; }

/* an editable value, with its provenance line still hanging underneath */
.prov-in { width: 100%; min-height: 38px; padding: 8px 10px; font: inherit; font-size: 15px;
  color: var(--ink); background: var(--surface); border: 1px solid var(--line);
  border-radius: 8px; line-height: 1.45; resize: vertical; }
.prov-in:focus { outline: none; border-color: var(--primary); box-shadow: var(--shadow-focus); }

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
/* Why there is nothing to print yet. Stated, so its absence reads as a rule
   rather than as a missing button. */
.dict-preprint { flex-basis: 100%; margin: 0; font-size: 13px; color: var(--ink-soft);
  line-height: 1.5; }

/* the conclusion dialog — the two endings that leave nothing digital behind */
.cdlg-scrim { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center;
  padding: 24px; background: rgba(23, 33, 31, .48); }
.cdlg { width: min(560px, 100%); max-height: 90vh; overflow: auto; background: var(--surface);
  border-radius: var(--radius-dialog); box-shadow: var(--shadow-popover); padding: 24px; }
.cdlg:focus { outline: none; }
.cdlg h2 { margin: 0; font-size: 21px; line-height: 1.25; color: var(--ink); }
.cdlg-lead { margin: 6px 0 16px; font-size: 14px; color: var(--ink-soft); }
.cdlg-choices { display: grid; gap: 8px; }
.cdlg-choice { display: flex; gap: 11px; align-items: flex-start; padding: 12px 14px;
  border: 1.5px solid var(--line); border-radius: 10px; cursor: pointer; }
.cdlg-choice.is-on { border-color: var(--primary); background: var(--primary-soft); }
.cdlg-choice input { margin-top: 3px; width: 17px; height: 17px; accent-color: var(--primary); }
.cdlg-choice strong { display: block; font-size: 15px; color: var(--ink); }
.cdlg-choice small { display: block; margin-top: 2px; font-size: 13px; color: var(--ink-soft);
  line-height: 1.45; }
/* Fixed slot: the confirm button must not move under the cursor when the
   doctor changes their mind (doc 14 principle 9). */
.cdlg-conseq { min-height: 118px; margin: 16px 0; padding: 13px 15px; border-radius: 10px;
  background: var(--bg); }
.cdlg-conseq[data-lossy="true"] { background: var(--accent-soft); }
.cdlg-conseq-h { display: flex; align-items: flex-start; gap: 8px; margin: 0; font-size: 14px;
  font-weight: 800; color: #7a4d0a; line-height: 1.5; }
.cdlg-conseq-h.is-ok { color: var(--primary-d); font-weight: 650; }
.cdlg-conseq-h svg { width: 17px; height: 17px; flex: none; margin-top: 2px; }
.cdlg-conseq ul { margin: 8px 0 0; padding-left: 26px; }
.cdlg-conseq li { font-size: 13.5px; color: #7a4d0a; line-height: 1.55; margin-bottom: 3px; }
.cdlg-note { display: block; }
.cdlg-note span { display: block; margin-bottom: 5px; font-size: 13px; color: var(--ink-soft); }
.cdlg-note textarea { width: 100%; padding: 9px 11px; font: inherit; font-size: 14px;
  border: 1px solid var(--line-strong); border-radius: 8px; background: var(--surface);
  color: var(--ink); line-height: 1.5; resize: vertical; }
.cdlg-err { margin: 12px 0 0; font-size: 14px; font-weight: 700; color: var(--danger); }
.cdlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.cdlg-cancel, .cdlg-go { min-height: 44px; font: inherit; font-size: 15px; font-weight: 800;
  padding: 11px 22px; border-radius: 11px; cursor: pointer; }
.cdlg-cancel { border: 1.5px solid var(--line-strong); background: var(--surface); color: var(--ink); }
.cdlg-go { border: none; background: var(--primary); color: #fff; }
.cdlg-cancel:disabled, .cdlg-go:disabled { opacity: .55; cursor: default; }

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

/* ---- the Reports tab (MRD2) ---------------------------------------------
   Two things carry the meaning here and everything else stays quiet:
   the draft stamp on an unverified reading, and the range track under a
   flagged value. Amber is the loudest colour on this surface — red belongs
   to the deterministic red-flag lane in the spine, and a lab value flagged
   against an unreviewed table must never borrow it. */
export const REPORTS_CSS = `
/* the spine's fifth slot */
.cx-reports { display: flex; align-items: baseline; gap: 9px; width: calc(100% - 44px);
  margin: 10px 22px 0; padding: 7px 10px; text-align: left; cursor: pointer;
  background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius-md);
  font: inherit; color: var(--ink-soft); }
.cx-reports:hover { border-color: var(--line-strong); color: var(--ink); }
.cx-reports.attn { background: var(--accent-soft); border-color: #e8c583; color: #7a4d0a; }
.cx-reports-l { font-size: 11px; font-weight: 800; text-transform: uppercase;
  letter-spacing: .07em; color: var(--ink); flex: none; }
.cx-reports.attn .cx-reports-l { color: #7a4d0a; }
.cx-reports-t { font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cx-reports-t.none { color: var(--text-muted); }

/* the tab badge */
.wtab-n.unread { background: var(--accent-soft); color: #7a4d0a; }

/* the tab body */
.reports { padding: 4px 0 8px; }
.rp-tally { margin: 0 0 14px; font-size: 14px; color: var(--ink-soft); }
.rp-tally strong { color: var(--ink); font-variant-numeric: tabular-nums; }
.rp-tally-flag { color: #7a4d0a; font-weight: 700; }

.rp-doc { border-top: 1px solid var(--line); }
.rp-doc:last-child { border-bottom: 1px solid var(--line); }
.rp-head { display: flex; align-items: center; justify-content: space-between; gap: 14px;
  width: 100%; padding: 13px 2px; background: none; border: none; cursor: pointer;
  font: inherit; text-align: left; color: var(--ink); }
.rp-head:hover { background: var(--surface-subtle); }
.rp-head-l { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.rp-head-l strong { font-size: 15px; font-weight: 700; }
.rp-when { font-size: 13px; color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.rp-head-r { flex: none; display: flex; align-items: center; gap: 8px; }
.rp-caret { color: var(--text-muted); font-size: 13px; }
.rp-chip { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em;
  border-radius: 999px; padding: 3px 9px; }
.rp-chip.flagged { background: var(--accent-soft); color: #7a4d0a; }
.rp-chip.draft { background: var(--bg); color: var(--ink-soft); border: 1px dashed var(--border-strong); }
.rp-chip.failed { background: var(--bg); color: var(--text-muted); border: 1px solid var(--border); }

.rp-body { padding: 2px 2px 20px; }

/* the draft stamp: dashed, because a draft is not a finished edge */
.rp-draft { display: flex; align-items: center; justify-content: space-between; gap: 16px;
  flex-wrap: wrap; padding: 11px 14px; margin-bottom: 12px;
  background: var(--surface-subtle); border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md); }
.rp-draft p { margin: 0; font-size: 13.5px; color: var(--ink-soft); line-height: 1.5; }
.rp-draft strong { color: var(--ink); }
.rp-verify { flex: none; padding: 8px 15px; font: inherit; font-size: 14px; font-weight: 700;
  cursor: pointer; color: #fff; background: var(--primary); border: 1px solid var(--primary-d);
  border-radius: var(--radius-control); }
.rp-verify:hover:not(:disabled) { background: var(--primary-d); }
.rp-verify:disabled { opacity: .6; cursor: default; }
.rp-verified { display: flex; align-items: center; gap: 8px; margin: 0 0 12px;
  font-size: 13.5px; color: var(--success); }
.rp-verified-mark { font-weight: 800; }

.rp-summary { margin: 0 0 16px; font-size: 15px; line-height: 1.65; color: var(--ink);
  white-space: pre-wrap; }
.rp-pending { margin: 0 0 14px; font-size: 14px; color: var(--ink-soft); line-height: 1.55; }

.rp-unread { padding: 12px 14px; margin-bottom: 14px; background: var(--bg);
  border-left: 3px solid var(--border-strong); border-radius: 0 var(--radius-md) var(--radius-md) 0; }
.rp-unread p { margin: 0; font-size: 14px; line-height: 1.55; color: var(--ink); }
.rp-unread-reason { margin-top: 5px !important; font-size: 13px !important;
  color: var(--ink-soft) !important; font-variant-numeric: tabular-nums; }
.rp-unread-note { margin-top: 6px !important; color: var(--ink-soft) !important; font-size: 13px !important; }

.rp-sec { margin-top: 18px; }
.rp-sec h3 { display: flex; align-items: baseline; gap: 9px; margin: 0 0 9px;
  font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: .08em;
  color: var(--ink-soft); border-bottom: 1px solid var(--line); padding-bottom: 6px; }
.rp-sec-n { font-size: 11px; font-weight: 700; letter-spacing: .03em; text-transform: none;
  color: #7a4d0a; background: var(--accent-soft); border-radius: 999px; padding: 2px 8px; }

.rp-fallback { margin: 0 0 10px; font-size: 13px; line-height: 1.55; color: #7a4d0a;
  background: var(--accent-soft); border-radius: var(--radius-md); padding: 8px 11px; }
.rp-fallback em { font-style: normal; font-weight: 700; }

.rp-values { width: 100%; border-collapse: collapse; font-size: 14px; }
.rp-values th { text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; color: var(--text-muted); padding: 0 10px 7px 0; }
.rp-values td { padding: 9px 10px 9px 0; border-top: 1px solid var(--line); vertical-align: top; }
.rp-values tr.weak td { color: var(--ink-soft); }
.vt-name { font-weight: 600; color: var(--ink); }
.vt-value { white-space: nowrap; }
.vt-num { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--ink); }
.vt-unit { font-weight: 500; color: var(--ink-soft); }
.vt-flag { display: inline-block; margin-left: 8px; font-size: 11px; font-weight: 800;
  text-transform: uppercase; letter-spacing: .04em; border-radius: 999px; padding: 2px 8px;
  background: var(--accent-soft); color: #7a4d0a; }
.vt-flag.critical_low, .vt-flag.critical_high { background: #f7dfc4; color: #7a3d0a; }
.vt-flag.unknown { background: var(--bg); color: var(--text-muted); }
.vt-ref { min-width: 150px; }
.vt-range { font-variant-numeric: tabular-nums; color: var(--ink-soft); }
.vt-src { display: block; font-size: 11px; color: var(--text-muted); margin-top: 1px; }
.vt-src.weak { color: #8a6516; font-weight: 600; }
.vt-norange { font-size: 13px; color: var(--text-muted); font-style: italic; }
.vt-page { text-align: right; }
.vt-pagebtn { font: inherit; font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums;
  cursor: pointer; color: var(--primary-d); background: var(--primary-soft);
  border: 1px solid transparent; border-radius: var(--radius-control); padding: 3px 9px; }
.vt-pagebtn:hover { border-color: var(--primary); }
.vt-nopage { color: var(--text-muted); }

/* the range track — this surface's one aesthetic risk */
.vt-track { position: relative; display: block; width: 118px; height: 5px; margin-top: 6px;
  background: var(--bg); border-radius: 3px; overflow: hidden; }
.vt-track-band { position: absolute; left: 33.3%; width: 33.4%; top: 0; bottom: 0;
  background: #cfe4dc; }
.vt-track.weak .vt-track-band { background: #e3e9e6; }
.vt-track-mark { position: absolute; top: -2px; width: 3px; height: 9px; margin-left: -1.5px;
  background: var(--accent); border-radius: 1px; }
.vt-track.weak .vt-track-mark { background: var(--border-strong); }

.rp-findings { margin: 0; padding-left: 18px; }
.rp-findings li { font-size: 14px; line-height: 1.6; color: var(--ink); margin-bottom: 5px; }
.rp-illegible { margin: 14px 0 0; font-size: 13px; line-height: 1.55; color: var(--ink-soft);
  background: var(--bg); border-radius: var(--radius-md); padding: 9px 11px; }
.rp-illegible strong { color: var(--ink); }
.rp-more { margin-top: 10px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer;
  color: var(--primary-d); background: none; border: none; padding: 4px 0; text-decoration: underline; }

/* the original pages */
.pg-strip { list-style: none; display: flex; flex-wrap: wrap; gap: 10px; margin: 0; padding: 0; }
.pg-thumb { position: relative; display: block; padding: 0; cursor: zoom-in; background: var(--bg);
  border: 1px solid var(--line); border-radius: var(--radius-md); overflow: hidden;
  width: 104px; height: 134px; }
.pg-thumb:hover { border-color: var(--primary); }
.pg-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.pg-n { position: absolute; left: 0; bottom: 0; font-size: 11px; font-weight: 700;
  color: #fff; background: rgba(22, 48, 43, .78); padding: 2px 7px; border-radius: 0 4px 0 0; }
.pg-load { width: 104px; height: 134px; background: var(--bg); border: 1px solid var(--line);
  border-radius: var(--radius-md); }
.pg-fail { margin: 0; font-size: 13px; line-height: 1.5; color: var(--ink-soft);
  background: var(--bg); border-radius: var(--radius-md); padding: 9px 11px; }
.pg-fail.gone { color: #7a4d0a; background: var(--accent-soft); }
.pg-none { margin: 0; font-size: 13px; color: var(--text-muted); }

.pg-zoom { position: fixed; inset: 0; z-index: 40; display: flex; flex-direction: column;
  background: rgba(12, 22, 20, .88); cursor: zoom-out; }
.pg-zoom-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 12px 18px; color: #fff; font-size: 14px; font-variant-numeric: tabular-nums;
  cursor: default; }
.pg-zoom-keys { display: flex; gap: 6px; }
.pg-zoom-keys button { font: inherit; font-size: 18px; line-height: 1; cursor: pointer;
  color: #fff; background: rgba(255, 255, 255, .12); border: 1px solid rgba(255, 255, 255, .25);
  border-radius: var(--radius-control); padding: 5px 13px; }
.pg-zoom-keys button:disabled { opacity: .35; cursor: default; }
.pg-zoom-img { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center;
  padding: 0 18px 18px; cursor: default; }
.pg-zoom-img img { max-width: 100%; max-height: 100%; object-fit: contain;
  background: #fff; border-radius: var(--radius-md); }
.pg-zoom-img .pg-load { width: 60vw; height: 70vh; }

.work-empty.err { color: #7a4d0a; }

@media (max-width: 900px) {
  .cx-reports { width: calc(100% - 32px); margin-left: 16px; margin-right: 16px; }
  .rp-values .vt-ref { min-width: 0; }
  .vt-track { width: 92px; }
}
`;

export const NOTE_CSS = `
/* ---- the ambient note dock (M4) ---------------------------------------- */
/* A button over the stage and a drawer under it. Neither covers the context
   spine: an observation is captured *while* reading, so the thing being read
   has to stay on screen. */

.nd-fab-wrap { position: fixed; right: 26px; bottom: 24px; z-index: 40;
  display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }

.nd-fab { position: relative; width: 56px; height: 56px; border-radius: 50%;
  display: grid; place-items: center; cursor: pointer;
  background: var(--brand); color: #fff; border: none;
  box-shadow: 0 6px 20px rgba(16,48,42,.22); transition: background .12s, transform .12s; }
.nd-fab:hover:not(:disabled) { transform: translateY(-1px); }
.nd-fab:disabled { opacity: .6; cursor: default; }
.nd-fab.is-rec { background: var(--danger); }
.nd-fab svg { position: relative; z-index: 1; }
.nd-spin { animation: nd-rot 1s linear infinite; }
@keyframes nd-rot { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .nd-spin { animation: none; } }

/* the level ring — real readings only (see NoteDock's header) */
.nd-ring { position: absolute; inset: -2px; width: 60px; height: 60px; transform: rotate(-90deg); }
.nd-ring-bg { fill: none; stroke: rgba(255,255,255,.22); stroke-width: 4; }
.nd-ring-fg { fill: none; stroke: #fff; stroke-width: 4; stroke-linecap: round;
  transition: stroke-dasharray .07s linear; }

.nd-fab-n { position: absolute; top: -2px; right: -2px; z-index: 2; min-width: 20px; height: 20px;
  padding: 0 5px; border-radius: 10px; display: grid; place-items: center;
  background: var(--accent); color: #4a2c05; font: 800 12px/1 var(--font-sans), sans-serif;
  font-variant-numeric: tabular-nums; border: 2px solid var(--canvas); }

.nd-fab-l { margin: 0; display: flex; flex-direction: column; align-items: flex-end; gap: 1px;
  font-size: 12px; color: var(--text-muted); text-align: right;
  /* It sits over the work area, so it needs its own ground to be legible on. */
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-md);
  padding: 5px 9px; box-shadow: 0 3px 10px rgba(16,48,42,.07); }
.nd-fab-what { font-weight: 700; color: var(--ink-soft); }
.nd-resume { background: none; border: none; padding: 0; cursor: pointer; font: inherit;
  color: #7a4d0a; font-weight: 700; text-decoration: underline; text-underline-offset: 2px; }

/* the live strip, while recording */
.nd-live { max-width: 340px; padding: 9px 12px; border-radius: var(--radius-md);
  background: var(--surface); border: 1px solid var(--line);
  box-shadow: 0 6px 18px rgba(16,48,42,.10); }
.nd-live-t { display: block; font: 800 13px/1 var(--font-sans), monospace;
  font-variant-numeric: tabular-nums; color: var(--danger); }
.nd-live-x { margin: 4px 0 0; font-size: 13px; line-height: 1.45; color: var(--ink-soft);
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }

.nd-err { position: fixed; right: 26px; bottom: 96px; z-index: 40; max-width: 340px;
  margin: 0; padding: 10px 13px; border-radius: var(--radius-md); font-size: 13px;
  background: var(--accent-soft); color: #7a4d0a; border: 1px solid #e8c583; }

/* ---- the drawer -------------------------------------------------------- */
/* Height, and the padding below, are one decision made twice.
   The first screenshot of this surface had the drawer at 62vh and it ate the
   spine — diagnosis, allergies and red flags all gone behind it, which is the
   exact failure Session B built the spine to fix and the reason this is a
   drawer rather than a tab. So: the drawer takes the bottom 52vh, the console
   gets matching padding underneath while it is open (so there is somewhere to
   scroll), and the sticky spine pins under the app bar in the 48vh that is
   left. The two numbers must move together. */
.nd-drawer { position: fixed; left: 0; right: 0; bottom: 0; z-index: 45;
  max-height: 52vh; display: flex; flex-direction: column;
  background: var(--surface); border-top: 1px solid var(--line);
  box-shadow: 0 -12px 34px rgba(16,48,42,.16); }
body[data-note-open="1"] .console { padding-bottom: 54vh; }

.nd-head { flex: none; display: flex; align-items: center; justify-content: space-between;
  gap: 14px; padding: 13px 24px; border-bottom: 1px solid var(--line); }
.nd-head-l { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; min-width: 0; }
.nd-head h2 { margin: 0; font-size: 16px; font-weight: 800; color: var(--ink); }
.nd-who { font-size: 14px; color: var(--ink-soft); }
.nd-badge { font: 700 11px/1 var(--font-sans), sans-serif; text-transform: uppercase;
  letter-spacing: .06em; padding: 5px 8px; border-radius: 6px;
  background: var(--accent-soft); color: #7a4d0a; }
.nd-badge.warn { background: var(--danger-soft); color: var(--danger); text-transform: none;
  letter-spacing: 0; font-weight: 600; font-size: 12px; }
/* Nothing was model-drafted, so it does not wear the amber that means it was. */
.nd-badge.plain { background: var(--canvas); color: var(--text-muted); }
.nd-x { border: none; background: none; cursor: pointer; color: var(--text-muted);
  display: grid; place-items: center; padding: 5px; border-radius: 6px; }
.nd-x:hover { background: var(--canvas); color: var(--ink); }

.nd-drawer-err { flex: none; margin: 0; padding: 9px 24px; font-size: 13px;
  background: var(--accent-soft); color: #7a4d0a; border-bottom: 1px solid #e8c583; }

.nd-body { flex: 1; min-height: 0; overflow-y: auto; display: grid;
  grid-template-columns: minmax(0, 4fr) minmax(0, 6fr); gap: 26px; padding: 16px 24px; }

/* what they said */
.nd-said h3 { margin: 0 0 8px; font: 800 11px/1 var(--font-sans), sans-serif;
  text-transform: uppercase; letter-spacing: .07em; color: var(--text-muted); }
.nd-said blockquote { margin: 0; padding: 0 0 0 13px; border-left: 3px solid var(--brand-soft);
  font-size: 15px; line-height: 1.6; color: var(--ink); }
.nd-said-none { margin: 0; font-size: 13px; line-height: 1.55; color: var(--text-muted); }
.nd-prov { margin: 12px 0 0; font-size: 11px; color: var(--text-muted); }
.nd-said .nd-tags, .nd-said .nd-tags-none { margin-top: 18px; padding-top: 14px;
  border-top: 1px solid var(--line); }

/* the fields */
.nd-fields { display: flex; flex-direction: column; gap: 11px; }
.nd-f { display: block; }
.nd-f-l { display: flex; align-items: baseline; gap: 8px; margin-bottom: 3px;
  font: 800 11px/1 var(--font-sans), sans-serif; text-transform: uppercase;
  letter-spacing: .06em; color: var(--text-muted); }
.nd-f-l em { font: 600 10px/1 var(--font-sans), sans-serif; font-style: normal;
  text-transform: none; letter-spacing: 0; color: var(--brand); }
.nd-f textarea { width: 100%; resize: vertical; padding: 8px 10px; font: inherit; font-size: 14px;
  line-height: 1.5; color: var(--ink); background: var(--canvas);
  border: 1px solid var(--line); border-radius: var(--radius-md); }
.nd-f textarea:focus { outline: 2px solid var(--brand); outline-offset: -1px;
  background: var(--surface); }
.nd-f.is-edited textarea { background: var(--surface); border-color: var(--brand); }

/* tags */
.nd-tags { display: flex; flex-direction: column; gap: 7px; margin-top: 3px; }
.nd-tags-none { margin: 3px 0 0; font-size: 12px; color: var(--text-muted); }
.nd-tagrow { display: flex; align-items: baseline; gap: 10px; }
.nd-tagrow-l { flex: none; width: 108px; font: 800 11px/1.3 var(--font-sans), sans-serif;
  text-transform: uppercase; letter-spacing: .06em; color: var(--text-muted); }
.nd-tagrow-c { display: flex; flex-wrap: wrap; gap: 6px; }
.nd-chip { display: inline-flex; align-items: center; gap: 5px; padding: 4px 5px 4px 9px;
  border-radius: 999px; background: var(--brand-soft); color: var(--brand-ink, #0A5A4A);
  font-size: 13px; font-weight: 600; }
.nd-chip-g { font: 800 11px/1 var(--font-sans), sans-serif; padding: 3px 5px; border-radius: 4px;
  background: rgba(255,255,255,.7); color: #0A5A4A; }
.nd-chip button { border: none; background: none; cursor: pointer; padding: 2px;
  display: grid; place-items: center; color: currentColor; opacity: .55; border-radius: 50%; }
.nd-chip button:hover { opacity: 1; background: rgba(255,255,255,.6); }

/* the footer, and the rule */
.nd-foot { flex: none; display: flex; align-items: center; justify-content: space-between;
  gap: 20px; padding: 12px 24px; border-top: 1px solid var(--line); background: var(--canvas); }
.nd-rule { margin: 0; max-width: 62ch; font-size: 12px; line-height: 1.5; color: var(--text-muted); }
.nd-rule strong { color: var(--ink-soft); font-weight: 700; }
.nd-foot-act { flex: none; display: flex; align-items: center; gap: 9px; }
.nd-later { border: 1px solid var(--line); background: var(--surface); color: var(--ink-soft);
  border-radius: var(--radius-md); padding: 9px 14px; font: inherit; font-size: 14px;
  font-weight: 600; cursor: pointer; }
.nd-later:hover:not(:disabled) { border-color: var(--border-strong); color: var(--ink); }
/* Green, not red: confirming a note you have read is safe expected progress.
   The same argument that kept the conclusion dialog's confirm green. */
.nd-confirm { border: none; background: var(--brand); color: #fff; border-radius: var(--radius-md);
  padding: 9px 18px; font: inherit; font-size: 14px; font-weight: 700; cursor: pointer; }
.nd-confirm:disabled { opacity: .45; cursor: default; }

@media (max-width: 900px) {
  .nd-body { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .nd-drawer { max-height: 72vh; }
  .nd-foot { flex-wrap: wrap; }
  .nd-tagrow { flex-direction: column; gap: 4px; }
  .nd-tagrow-l { width: auto; }
}
`;

export const RESEARCH_CSS = `
/* ---- the research tab (M5) --------------------------------------------- */
/* A tab, not a dock — reading an evidence summary *is* the thing the doctor is
   doing, it wants the width, and the note dock already owns the bottom of the
   screen. See ResearchTab's header for the argument. */

.rsx { display: flex; flex-direction: column; gap: 16px; padding: 4px 0 8px; }

/* ---- what will be sent (first on the screen, deliberately) -------------- */
.rsx-ctx { border: 1px solid var(--line); border-radius: var(--radius-lg);
  background: var(--surface); padding: 14px 16px; }
.rsx-ctx-h { display: flex; align-items: baseline; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; }
.rsx-ctx-h h3 { margin: 0; font-size: 14px; font-weight: 800; color: var(--ink);
  letter-spacing: .01em; }
.rsx-ctx-n { margin: 0; font-size: 12px; color: var(--text-muted);
  font-variant-numeric: tabular-nums; }
.rsx-ctx-phi { margin: 5px 0 11px; font-size: 12px; line-height: 1.5; color: var(--text-muted); }

.rsx-items { list-style: none; margin: 0; padding: 0;
  display: flex; flex-direction: column; gap: 9px; }
.rsx-items > li { padding-left: 1px; }
.rsx-items label { display: flex; align-items: flex-start; gap: 9px; cursor: pointer; }
.rsx-items input { margin: 3px 0 0; flex: none; width: 15px; height: 15px;
  accent-color: var(--brand); cursor: pointer; }
.rsx-item-t { font-size: 14px; line-height: 1.5; color: var(--ink); }
.rsx-item-src { margin: 2px 0 0 24px; font-size: 12px; line-height: 1.45; color: var(--text-muted); }
.rsx-item-caveat { color: #7a4d0a; font-weight: 600; }

/* The deliberate risk: a line the doctor turns off is struck, not removed.
   A withheld line and a line this patient never had must not look identical —
   the same rule the spine's "No red flags fired" follows. */
.rsx-items > li.off .rsx-item-t { text-decoration: line-through;
  text-decoration-thickness: 1px; color: var(--text-muted); }
.rsx-items > li.off .rsx-item-src { opacity: .55; }

.rsx-absent { list-style: none; margin: 12px 0 0; padding: 10px 0 0;
  border-top: 1px dashed var(--line); display: flex; flex-direction: column; gap: 4px; }
.rsx-absent li { font-size: 12px; line-height: 1.5; color: var(--text-muted); }
.rsx-absent-l { font-weight: 700; color: var(--ink-soft); }

/* ---- the framing ------------------------------------------------------- */
/* Above the conversation, not under it in small print. It is the most
   important sentence on this screen. Amber, never red: red on this console
   belongs to the deterministic red-flag lane in the spine. */
.rsx-frame { margin: 0; padding: 10px 14px; border-radius: var(--radius-md);
  background: var(--accent-soft); border: 1px solid #e8c583; color: #7a4d0a;
  font-size: 13px; line-height: 1.55; }
.rsx-frame strong { font-weight: 800; }

/* ---- the conversation -------------------------------------------------- */
.rsx-thread { display: flex; flex-direction: column; gap: 16px; }

.rsx-empty { margin: 0; padding: 18px 2px; font-size: 14px; color: var(--text-muted); }

.rsx-suggest { display: flex; flex-direction: column; align-items: flex-start; gap: 7px; }
.rsx-suggest-l { margin: 0 0 1px; font-size: 12px; font-weight: 700; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .05em; }
.rsx-suggest button { text-align: left; font: inherit; font-size: 13px; cursor: pointer;
  padding: 8px 13px; border-radius: var(--radius-md); color: var(--ink-soft);
  background: var(--surface); border: 1px solid var(--line); }
.rsx-suggest button:hover:not(:disabled) { border-color: var(--border-strong); color: var(--ink); }
.rsx-suggest button:disabled { opacity: .5; cursor: default; }

.rsx-turn { border-left: 2px solid var(--line); padding-left: 15px; }
.rsx-q { margin: 0 0 9px; font-size: 14px; font-weight: 700; color: var(--ink); line-height: 1.5; }
.rsx-a p { margin: 0 0 9px; font-size: 14px; line-height: 1.62; color: var(--ink-soft); }
.rsx-a p:last-child { margin-bottom: 0; }

.rsx-turn-f { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 9px; }
.rsx-model { font-size: 11px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.rsx-sent-toggle { background: none; border: none; padding: 0; cursor: pointer; font: inherit;
  font-size: 12px; color: var(--text-muted); text-decoration: underline;
  text-underline-offset: 2px; }
.rsx-sent-toggle:hover { color: var(--ink-soft); }

.rsx-sent { list-style: none; margin: 8px 0 0; padding: 9px 12px; border-radius: var(--radius-md);
  background: var(--canvas); border: 1px solid var(--line);
  display: flex; flex-direction: column; gap: 4px; }
.rsx-sent li { font-size: 12px; line-height: 1.5; color: var(--text-muted); }

.rsx-thinking { margin: 0; font-size: 13px; color: var(--text-muted); }

/* ---- the halt states --------------------------------------------------- */
/* Both close the composer. Neither queues anything. */
.rsx-halt { margin: 0; padding: 11px 14px; border-radius: var(--radius-md);
  font-size: 13px; line-height: 1.55;
  background: var(--accent-soft); border: 1px solid #e8c583; color: #7a4d0a; }
.rsx-halt strong { font-weight: 800; }

.rsx-err { margin: 0; font-size: 13px; color: var(--danger); }

/* ---- the question box -------------------------------------------------- */
.rsx-composer { display: flex; flex-direction: column; gap: 9px; }
.rsx-composer textarea { width: 100%; resize: vertical; font: inherit; font-size: 14px;
  line-height: 1.5; padding: 11px 13px; border-radius: var(--radius-md);
  border: 1px solid var(--line); background: var(--surface); color: var(--ink); }
.rsx-composer textarea:focus { outline: 2px solid var(--brand); outline-offset: 1px;
  border-color: transparent; }
.rsx-composer-r { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.rsx-budget { font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.rsx-budget.low { color: #7a4d0a; font-weight: 700; }
.rsx-ask { border: none; background: var(--brand); color: #fff; border-radius: var(--radius-md);
  padding: 9px 20px; font: inherit; font-size: 14px; font-weight: 700; cursor: pointer; }
.rsx-ask:disabled { opacity: .45; cursor: default; }

@media (max-width: 900px) {
  .rsx-ctx-h { flex-direction: column; gap: 2px; }
}
`;
