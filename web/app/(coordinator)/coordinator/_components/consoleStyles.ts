// All coordinator-console CSS in one place (built on the doc 04 §1 tokens). The
// console is a staff surface, so it's allowed dense tables — but it keeps the
// same green/marigold system, and downtime repaints the app bar marigold.

export const CONSOLE_CSS = `
.console { min-height: 100vh; background: var(--bg); color: var(--ink);
  font-family: var(--font-sans), "Noto Sans", "Noto Sans Devanagari", system-ui, sans-serif; }

/* app bar */
.appbar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center;
  justify-content: space-between; gap: 16px; padding: 12px 22px;
  background: var(--surface); border-bottom: 1px solid var(--line); }
.appbar-l { display: flex; align-items: center; gap: 18px; min-width: 0; }
.appbar .logo { color: var(--primary); font-size: 22px; }
.appbar strong { font-size: 17px; color: var(--ink); }
.tabs { display: flex; gap: 4px; flex-wrap: wrap; }
.tab { border: none; background: none; padding: 8px 14px; border-radius: 999px; cursor: pointer;
  font-size: 14px; font-weight: 600; color: var(--ink-soft); }
.tab:hover { background: var(--primary-soft); color: var(--primary-d); }
.tab.on { background: var(--primary); color: #fff; }
.appbar-r { display: flex; align-items: center; gap: 10px; }
.downtime-toggle { border: 1.5px solid var(--accent); background: var(--accent-soft);
  color: #7a4d0a; font-weight: 700; padding: 9px 16px; border-radius: 12px; cursor: pointer;
  font-size: 14px; }
.downtime-toggle.active { background: #7a4d0a; color: #fff; border-color: #7a4d0a; }
.signout { border: none; background: none; color: var(--ink-soft); cursor: pointer; font-size: 14px; }

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
.queue-grid { display: grid; gap: 18px; padding: 22px;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 440px), 1fr)); align-items: start; }
.dept { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 18px; }
.dept-head { display: flex; justify-content: space-between; align-items: center; }
.dept-head h2 { margin: 0; font-size: 19px; color: var(--ink); }
.call-next { background: var(--primary); color: #fff; border: none; border-radius: 12px;
  padding: 9px 16px; font-weight: 700; cursor: pointer; font-size: 14px; }
.call-next:hover { background: var(--primary-d); }
.dept-sub { color: var(--ink-soft); font-size: 13px; margin: 4px 0 12px; }
.entries { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.entry { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center;
  border: 1px solid var(--line); border-radius: 16px; padding: 10px 12px; background: #fff; }
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
.chip-urgent { background: var(--accent); color: #3a2606; font-weight: 700; font-size: 12px;
  padding: 3px 10px; border-radius: 999px; }
.chip-flag { background: var(--danger-soft); color: var(--danger); font-weight: 700; font-size: 12px;
  padding: 3px 10px; border-radius: 999px; }
.state-badge { font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  background: #eef3f1; color: var(--ink-soft); }
.state-badge.s-called { background: var(--primary-soft); color: var(--primary-d); }
.state-badge.s-in_consult { background: var(--primary); color: #fff; }
.state-badge.s-lab_requeue { background: #e4ecf6; color: #3b567a; }
.chief { font-size: 14px; color: var(--ink); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.actions { display: flex; align-items: center; gap: 6px; }
.act { border: none; border-radius: 10px; padding: 8px 12px; font-weight: 700; font-size: 13px;
  cursor: pointer; }
.act.primary { background: var(--primary); color: #fff; }
.act.primary:hover { background: var(--primary-d); }
.act.ghost { background: #fff; border: 1px solid var(--line); color: var(--ink-soft); }
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
}
`;
