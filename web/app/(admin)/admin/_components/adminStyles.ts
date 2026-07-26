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

/* -- the tree editor (S18-late) ---------------------------------------------
   The one deliberate move on this surface: the questions are drawn as a spine,
   each branch indented under the option that leads to it, so an author reads the
   tree in the order a patient walks it rather than as a list of rows. Everything
   else here is quiet on purpose — this is the doctor console's clinical spine
   borrowed for authored content, not a second idea. */
.admin .editor-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 12px 14px; position: sticky; top: 60px; z-index: 4; margin-bottom: 14px; }
.admin .editor-bar .spacer { flex: 1; }
.admin .editor-bar code, .admin .editor code { font-size: 12px; color: var(--ink-soft); }
.admin .langs { display: flex; gap: 2px; border: 1px solid var(--line); border-radius: 999px; padding: 2px; }
.admin .langs button { border: none; background: none; border-radius: 999px; padding: 5px 11px;
  font-size: 12px; font-weight: 700; text-transform: uppercase; color: var(--ink-soft); cursor: pointer; }
.admin .langs button.on { background: var(--primary); color: #fff; }
.admin button.action.publish { background: var(--accent); color: #4a2f04; }
.admin .editor-note { margin: 0 0 14px; }

.admin .spine { list-style: none; margin: 0 0 24px; padding: 0; }
.admin .station { position: relative; padding: 0 0 14px 22px; }
.admin .station .rail { position: absolute; left: 6px; top: 6px; bottom: -6px; width: 2px;
  background: var(--line); }
.admin .station:last-child .rail { bottom: 50%; }
.admin .station::before { content: ""; position: absolute; left: 1px; top: 16px; width: 12px; height: 12px;
  border-radius: 50%; background: var(--surface); border: 2px solid var(--line); z-index: 1; }
.admin .station.flagged::before { border-color: var(--danger); background: var(--danger-soft); }
.admin .station-body { background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 12px 14px; }
.admin .station.flagged .station-body { border-color: #f1c9c2; }
.admin .station header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.admin .station .kind { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--ink-soft); background: var(--line); border-radius: 999px; padding: 2px 8px; }
.admin .station .via { font-size: 12px; color: var(--ink-soft); font-style: italic; }
.admin .stamp { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em;
  padding: 2px 8px; border-radius: 4px; background: var(--danger-soft); color: var(--danger); }
.admin .stamp.semi { background: var(--accent-soft); color: #8a5a10; }

.admin .field { display: block; }
.admin .field > span { display: block; font-size: 11px; text-transform: uppercase;
  letter-spacing: .04em; color: var(--ink-soft); margin-bottom: 4px; }
.admin textarea { width: 100%; font: inherit; font-size: 14px; padding: 8px 10px;
  border: 1px solid var(--line); border-radius: 10px; background: var(--surface);
  color: var(--ink); resize: vertical; line-height: 1.6; }
.admin .options { margin-top: 10px; display: grid; gap: 6px; }
.admin .opt { display: grid; grid-template-columns: 110px 1fr 150px; gap: 8px; align-items: center; }
.admin .opt-id { font-size: 11px; color: var(--ink-soft); font-family: ui-monospace, monospace;
  overflow: hidden; text-overflow: ellipsis; }
.admin .opt input { width: 100%; line-height: 1.6; }
.admin .opt .goes { font-size: 11px; color: var(--ink-soft); text-align: right;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.admin fieldset.flag { margin: 12px 0 0; border: 1px solid #f1c9c2; border-radius: 12px;
  padding: 10px 12px; background: #fdf6f5; }
.admin fieldset.flag legend { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--danger); padding: 0 6px; }
.admin .flag-row { display: flex; gap: 8px; margin-bottom: 8px; }
.admin .flag-row input { flex: 1; }

.admin .testrun { background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 16px; }
.admin .testrun h3 { font-size: 14px; margin: 0 0 4px; }
.admin .try-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px; margin: 12px 0; }
.admin .try { display: grid; gap: 4px; font-size: 12px; color: var(--ink-soft); }
.admin .try select, .admin .try input { width: 100%; }
.admin .try-out { margin-top: 12px; border-top: 1px solid var(--line); padding-top: 12px; }
.admin ul.flags { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 6px; }

/* Question sets read as plain cards, not as seven marigold warnings: the accent
   is this system's "look at this" colour and spending it on every set would leave
   nothing louder for the rule that actually rings a phone. The red/amber pills
   carry the alarm. */
.admin .set-card { background: var(--surface); border: 1px solid var(--line);
  border-radius: 12px; padding: 12px 16px; margin-bottom: 10px; }
.admin .set-card ul { margin: 8px 0 0; padding-left: 18px; }
.admin .set-card li { margin: 3px 0; }

/* Credential fields (S-GL.1). Every input is a password field with a placeholder
   of "unchanged" rather than a value, because there is no value to render — the
   API never returns one. The grid is wide enough that a Meta token is not typed
   into a box the width of a postcode. */
.admin .cred-fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px; margin: 12px 0; }
.admin .cred-fields input { width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px; }

.admin .bank-editor { margin-top: 12px; }
.admin textarea.doc { width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px; line-height: 1.5; background: #0e1b18; color: #d7e8e2; border-color: #0e1b18; }
`;
