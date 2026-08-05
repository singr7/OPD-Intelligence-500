// All coordinator-console CSS in one place (built on the doc 04 §1 tokens). The
// console is a staff surface, so it's allowed dense tables — but it keeps the
// same green/marigold system, and downtime repaints the app bar marigold.

export const CONSOLE_CSS = `
.console { min-height: 100vh; background: var(--canvas); color: var(--text);
  font-family: var(--font-sans), "Noto Sans", "Noto Sans Devanagari", system-ui, sans-serif; }

/* app bar */
.appbar { position: sticky; top: 0; z-index: 10; min-height: 64px; display: flex; align-items: center;
  justify-content: space-between; gap: 16px; padding: 8px 24px;
  background: var(--shell); border-bottom: 1px solid var(--shell-raised); color: #fff; }
.appbar-l { display: flex; align-items: center; gap: 18px; min-width: 0; }
.appbar .logo { width: 34px; height: 34px; display: grid; place-items: center;
  border-radius: 7px; background: var(--brand); color: #fff; }
.appbar .logo svg { width: 18px; height: 18px; }
.app-title { display: grid; gap: 0; }
.appbar strong { font-size: 15px; color: #fff; }
.app-title small { color: #aebdb8; font-size: 11px; }
.tabs { display: flex; gap: 4px; flex-wrap: wrap; }
.tab { min-height: 40px; border: none; border-bottom: 2px solid transparent; background: none;
  padding: 0 12px; cursor: pointer; font-size: 13px; font-weight: 650; color: #aebdb8; }
.tab:hover { color: #fff; }
.tab.on { border-bottom-color: #68d4b6; color: #fff; }
.appbar-r { display: flex; align-items: center; gap: 10px; }
.downtime-toggle { min-height: 40px; display: inline-flex; align-items: center; gap: 8px;
  border: 1px solid #87652d; background: #3b3425; color: #ffd891; font-weight: 700;
  padding: 0 14px; border-radius: var(--radius-control); cursor: pointer; font-size: 13px; }
.downtime-toggle svg { width: 16px; height: 16px; }
.downtime-toggle.active { background: #7a4d0a; color: #fff; border-color: #7a4d0a; }
.signout { border: none; background: none; color: #aebdb8; cursor: pointer; font-size: 13px; }

/* downtime skin: the whole bar goes marigold */
.console.is-downtime .appbar { background: var(--accent); border-bottom-color: #c47c17; }
.console.is-downtime .appbar strong, .console.is-downtime .appbar .logo,
.console.is-downtime .tab { color: #3a2606; }
.console.is-downtime .tab.on { background: #3a2606; color: #fff; }
.console.is-downtime .downtime-toggle.active { background: #3a2606; border-color: #3a2606; }
.downtime-banner { background: var(--accent); color: #3a2606; font-weight: 800; text-align: center;
  padding: 10px; letter-spacing: .02em; font-size: 15px; }

.err-toast { margin: 14px 22px 0; background: var(--danger-soft); color: var(--danger);
  border-radius: 12px; padding: 10px 14px; font-weight: 600; font-size: 14px; }
.loading, .empty-state { padding: 60px 22px; text-align: center; color: var(--ink-soft);
  font-size: 16px; }

/* queue */
.queue-page { max-width: 1480px; margin: 0 auto; padding: 24px; }
.metric-strip { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px;
  overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius-panel); background: var(--line); }
.metric { min-height: 86px; display: flex; align-items: center; gap: 12px; padding: 16px;
  background: var(--surface); }
.metric > svg { width: 20px; height: 20px; color: var(--text-faint); }
.metric span { display: grid; gap: 3px; }
.metric small { color: var(--text-muted); font-size: 12px; }
.metric strong { color: var(--text); font-size: 25px; line-height: 1; font-variant-numeric: tabular-nums; }
.metric.tone-danger > svg, .metric.tone-danger strong { color: var(--danger); }
.metric.tone-attention > svg, .metric.tone-attention strong { color: #7a4d0a; }
.metric.tone-info > svg { color: var(--info); }
.metric.tone-success > svg { color: var(--brand); }
.queue-heading { display: flex; justify-content: space-between; align-items: end; margin: 26px 0 12px; }
.queue-heading h1 { margin: 0; font-size: 22px; }
.queue-heading p { margin: 5px 0 0; color: var(--text-muted); font-size: 13px; }
.queue-grid { display: grid; gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 440px), 1fr)); align-items: start; }
.dept { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-panel);
  padding: 16px; }
.dept-head { display: flex; justify-content: space-between; align-items: center; }
.dept-head h2 { margin: 0; font-size: 19px; color: var(--ink); display: flex; align-items: baseline;
  gap: 10px; flex-wrap: wrap; }
.dept-doc { font-style: normal; font-size: 14px; font-weight: 600; color: var(--ink-soft); }
.call-next { min-height: 38px; background: var(--brand); color: #fff; border: none;
  border-radius: var(--radius-control); padding: 0 14px; font-weight: 700; cursor: pointer; font-size: 13px; }
.call-next:hover { background: var(--primary-d); }
.dept-sub { color: var(--ink-soft); font-size: 13px; margin: 4px 0 12px; }
.entries { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.entry { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center;
  border: 1px solid var(--line); border-radius: 7px; padding: 10px 11px; background: #fff; }
.entry[draggable=true] { cursor: grab; }
.entry.urgent { border-color: var(--accent); background: #fffaf0; }
.entry.state-called { box-shadow: inset 0 0 0 2px var(--primary-soft); }
.entry.state-in_consult { opacity: .95; background: var(--primary-soft); }
.entry.state-lab_requeue { background: #f4f7fb; }
.tok-col { display: flex; align-items: center; gap: 8px; }
.drag { color: var(--line); font-size: 18px; }
.tok { font-size: 30px; font-weight: 800; color: var(--primary-d); font-variant-numeric: tabular-nums;
  min-width: 52px; text-align: center; }
.mid { min-width: 0; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 4px; }
.chip-urgent { display: inline-flex; align-items: center; gap: 4px; background: var(--attention-soft);
  color: #7a4d0a; font-weight: 700; font-size: 12px; padding: 3px 9px; border-radius: 999px; }
.chip-urgent svg { width: 13px; height: 13px; }
.chip-flag { background: var(--danger-soft); color: var(--danger); font-weight: 700; font-size: 12px;
  padding: 3px 10px; border-radius: 999px; }
.state-badge { font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  background: #eef3f1; color: var(--ink-soft); }
.state-badge.s-called { background: var(--primary-soft); color: var(--primary-d); }
.state-badge.s-in_consult { background: var(--primary); color: #fff; }
.state-badge.s-lab_requeue { background: #e4ecf6; color: #3b567a; }
.pname { font-size: 15px; font-weight: 700; color: var(--ink); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.chief { font-size: 14px; color: var(--ink-soft); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.actions { display: flex; align-items: center; gap: 6px; }
.act { border: none; border-radius: 10px; padding: 8px 12px; font-weight: 700; font-size: 13px;
  cursor: pointer; }
.act.primary { background: var(--primary); color: #fff; }
.act.primary:hover { background: var(--primary-d); }
.act.ghost { background: #fff; border: 1px solid var(--line); color: var(--ink-soft); }
.chip-doc { background: var(--brand-soft); color: var(--brand-strong); font-weight: 700;
  font-size: 12px; padding: 3px 10px; border-radius: 999px; }
.chip-unassigned { background: var(--attention-soft); color: #7a4d0a; font-weight: 700;
  font-size: 12px; padding: 3px 10px; border-radius: 999px; }
.chip-match { background: var(--info-soft); color: var(--info); font-weight: 700; font-size: 12px;
  padding: 3px 10px; border-radius: 999px; }

/* assign panel (AR3) — opens under its row, inside the queue it is read against */
/* min-width:0 throughout: the doctor option text ("Dr. X · MD, DM · on duty
   today") is long, and a select sized to its widest option will otherwise blow
   the grid column — and with it the department card — wider than the page.
   (No backticks in this file: the whole stylesheet is a JS template literal.) */
.assign { grid-column: 1 / -1; min-width: 0; margin-top: 10px; padding: 12px; border-radius: 7px;
  background: var(--surface-subtle); border: 1px solid var(--line); display: grid; gap: 10px; }
.assign-link { display: grid; gap: 8px; padding-bottom: 10px; border-bottom: 1px solid var(--line);
  font-size: 13px; color: var(--text-muted); }
.assign-link-btns { display: flex; gap: 8px; flex-wrap: wrap; }
.assign-row { display: grid; grid-template-columns: 1fr 1.4fr; gap: 10px; min-width: 0; }
.assign-row label { display: grid; gap: 4px; font-size: 12px; font-weight: 700;
  color: var(--text-muted); min-width: 0; }
.assign-row select { width: 100%; min-width: 0; min-height: 40px; padding: 0 10px; font-size: 14px; color: var(--text);
  border: 1px solid var(--border-strong); border-radius: var(--radius-control); background: #fff;
  font-family: inherit; }
.assign-row select:disabled { background: #eef2f0; color: var(--text-faint); }
.assign-warn { margin: 0; font-size: 12.5px; font-weight: 600; color: #7a4d0a;
  background: var(--attention-soft); border-radius: var(--radius-control); padding: 8px 10px; }
.assign-err { margin: 0; font-size: 13px; font-weight: 600; color: var(--danger); }
.assign-actions { display: flex; justify-content: flex-end; gap: 8px; }
.assign.reissued { background: var(--accent); border-color: #c47c17; color: #2a1a00;
  grid-template-columns: 1fr auto auto; align-items: center; }
.reissue-copy { display: grid; gap: 2px; }
.reissue-copy strong { font-size: 14px; }
.reissue-copy span { font-size: 12.5px; }
.reissue-token { font-size: 34px; font-weight: 800; font-variant-numeric: tabular-nums;
  line-height: 1; }
.assign.reissued .act.primary { background: #2a1a00; color: var(--accent); }

.nudge { display: flex; flex-direction: column; gap: 2px; }
.nudge button { border: 1px solid var(--line); background: #fff; border-radius: 8px; width: 26px;
  height: 20px; cursor: pointer; color: var(--ink-soft); line-height: 1; }

/* reconciliation */
.recon { padding: 22px; }
.recon-lead { color: var(--ink-soft); font-size: 14px; margin: 0 0 14px; }
.recon-table { width: 100%; border-collapse: collapse; background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; font-size: 14px; }
.recon-table th { text-align: left; background: #eef3f1; color: var(--ink-soft); font-size: 12px;
  text-transform: uppercase; letter-spacing: .04em; padding: 10px 14px; }
.recon-table td { padding: 12px 14px; border-top: 1px solid var(--line); }
.recon-table tr.has-flag td { background: #fffaf0; }
.recon-table .tok { font-weight: 800; color: var(--primary-d); font-variant-numeric: tabular-nums; }
.src { font-size: 12px; font-weight: 700; padding: 3px 9px; border-radius: 999px;
  background: #eef3f1; color: var(--ink-soft); }
.src-kiosk { background: var(--primary-soft); color: var(--primary-d); }
.src-paper { background: var(--accent-soft); color: #7a4d0a; }
.recon-table .when { color: var(--ink-soft); font-variant-numeric: tabular-nums; }

/* paper entry */
.paper { padding: 22px; display: flex; justify-content: center; }
.paper-form { width: 100%; max-width: 620px; background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow); padding: 26px; }
.paper-form h2 { margin: 0 0 4px; font-size: 20px; }
.paper-lead { color: var(--ink-soft); font-size: 14px; margin: 0 0 18px; }
.paper-form label { display: block; font-size: 13px; font-weight: 600; color: var(--ink-soft);
  margin-bottom: 14px; }
.paper-form input, .paper-form select, .paper-form textarea { width: 100%; margin-top: 6px;
  font-size: 16px; padding: 11px 13px; border: 1.5px solid var(--line); border-radius: 12px;
  color: var(--ink); font-family: inherit; }
.paper-form .row { display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 12px; }
.paper-form .check { display: flex; align-items: center; gap: 10px; font-weight: 600; color: var(--ink); }
.paper-form .check input { width: auto; margin: 0; }
.paper-form button { width: 100%; margin-top: 6px; background: var(--primary); color: #fff; border: none;
  border-radius: 12px; padding: 13px; font-weight: 700; font-size: 16px; cursor: pointer; }
.paper-form button:disabled { opacity: .6; }
.paper-form .ok { color: var(--primary-d); font-weight: 600; margin-top: 12px; }
.paper-form .bad { color: var(--danger); font-weight: 600; margin-top: 12px; }

/* print */
.print-tab { padding: 22px; display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); align-items: start; }
.print-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 22px; }
.print-card h2 { margin: 0 0 8px; font-size: 18px; }
.print-card p { color: var(--ink-soft); font-size: 14px; line-height: 1.55; margin: 0 0 16px; }
.print-card button { background: var(--primary); color: #fff; border: none; border-radius: 12px;
  padding: 11px 16px; font-weight: 700; cursor: pointer; font-size: 14px; }
.print-card .kiosk-id { display: block; font-size: 13px; color: var(--ink-soft); margin-bottom: 14px; }
.print-card .kiosk-id input { display: block; margin-top: 6px; width: 100%; padding: 10px 12px;
  border: 1.5px solid var(--line); border-radius: 10px; font-size: 15px; }
.print-err { grid-column: 1 / -1; color: var(--danger); font-weight: 600; }

@media (max-width: 560px) {
  .paper-form .row { grid-template-columns: 1fr; }
  .entry { grid-template-columns: auto 1fr; }
  .actions { grid-column: 1 / -1; justify-content: flex-end; }
  .assign-row { grid-template-columns: 1fr; }
  .assign.reissued { grid-template-columns: 1fr; }
}
@media (max-width: 1000px) {
  .appbar { align-items: flex-start; }
  .appbar-l { flex-wrap: wrap; }
  .tabs { order: 3; width: 100%; overflow-x: auto; flex-wrap: nowrap; }
  .metric-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 680px) {
  .appbar { padding: 8px 12px; }
  .app-title, .signout { display: none; }
  .downtime-toggle { padding: 0 10px; }
  .queue-page { padding: 14px; }
  .metric-strip { grid-template-columns: 1fr 1fr; }
  .metric:last-child { grid-column: 1 / -1; }
}
`;
