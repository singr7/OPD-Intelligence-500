// One stylesheet for the admin console, injected once by Console. Uses the doc 04
// design tokens (globals.css) so admin looks of a piece with the other surfaces,
// but denser: this is a back-office tool for an operator at a desk, not an
// audio-first kiosk, so it trades whitespace for information density.

export const ADMIN_CSS = `
.admin { min-height: 100vh; background: var(--bg); color: var(--ink);
  font-size: 14px; }
.admin header { display: flex; align-items: center; gap: 16px; padding: 16px 24px;
  background: var(--surface); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 5; }
.admin header .badge { font-size: 12px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--accent); background: var(--accent-soft);
  border-radius: 999px; padding: 5px 12px; }
.admin header h1 { font-size: 18px; margin: 0; }
.admin header .spacer { flex: 1; }
.admin header button { background: none; border: 1px solid var(--line); border-radius: 10px;
  padding: 8px 14px; color: var(--ink-soft); cursor: pointer; font-size: 13px; }

.admin nav { display: flex; gap: 4px; padding: 0 24px; background: var(--surface);
  border-bottom: 1px solid var(--line); overflow-x: auto; }
.admin nav button { background: none; border: none; border-bottom: 2px solid transparent;
  padding: 12px 14px; color: var(--ink-soft); cursor: pointer; font-size: 14px; font-weight: 600;
  white-space: nowrap; }
.admin nav button.active { color: var(--primary); border-bottom-color: var(--primary); }

.admin main { padding: 24px; max-width: 1180px; margin: 0 auto; }
.admin h2 { font-size: 16px; margin: 0 0 12px; }
.admin section { margin-bottom: 28px; }
.admin .muted { color: var(--ink-soft); }

.admin .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.admin .card { background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 16px; }
.admin .card .label { font-size: 12px; color: var(--ink-soft); text-transform: uppercase;
  letter-spacing: .04em; margin-bottom: 6px; }
.admin .card .value { font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; }
.admin .card .sub { font-size: 12px; color: var(--ink-soft); margin-top: 4px; }

.admin table { width: 100%; border-collapse: collapse; background: var(--surface);
  border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.admin th, .admin td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line);
  font-variant-numeric: tabular-nums; }
.admin th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--ink-soft);
  background: #fafcfb; }
.admin td.num, .admin th.num { text-align: right; }
.admin tr:last-child td { border-bottom: none; }

.admin .bar { height: 8px; border-radius: 4px; background: var(--line); overflow: hidden; }
.admin .bar > span { display: block; height: 100%; background: var(--primary); }
.admin .bar.warn > span { background: var(--accent); }
.admin .bar.bad > span { background: var(--danger); }

.admin .pill { display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 8px;
  border-radius: 999px; text-transform: uppercase; letter-spacing: .03em; }
.admin .pill.ok { background: var(--primary-soft); color: var(--primary-d); }
.admin .pill.approaching { background: var(--accent-soft); color: #8a5a10; }
.admin .pill.breached, .admin .pill.bad { background: var(--danger-soft); color: var(--danger); }
.admin .pill.uncapped, .admin .pill.draft { background: var(--line); color: var(--ink-soft); }
.admin .pill.published { background: var(--primary-soft); color: var(--primary-d); }

.admin .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.admin select, .admin input { font-size: 13px; padding: 8px 10px; border: 1px solid var(--line);
  border-radius: 10px; background: var(--surface); color: var(--ink); }
.admin button.action { background: var(--primary); color: #fff; border: none; border-radius: 10px;
  padding: 8px 14px; font-weight: 600; cursor: pointer; font-size: 13px; }
.admin button.action:disabled { opacity: .5; cursor: default; }
.admin button.ghost { background: none; border: 1px solid var(--line); color: var(--ink);
  border-radius: 10px; padding: 7px 12px; cursor: pointer; font-size: 13px; }
.admin .error { color: var(--danger); font-size: 13px; margin: 8px 0; }
.admin .notice { background: var(--accent-soft); border: 1px solid #f0d49a; border-radius: 12px;
  padding: 14px 16px; color: #7a4e0c; }
.admin pre { background: #0e1b18; color: #d7e8e2; border-radius: 12px; padding: 14px;
  overflow-x: auto; font-size: 12px; line-height: 1.5; max-height: 420px; }
.admin .spark { display: flex; align-items: flex-end; gap: 2px; height: 60px; }
.admin .spark > i { flex: 1; background: var(--primary); border-radius: 2px 2px 0 0; min-height: 2px; }
`;
