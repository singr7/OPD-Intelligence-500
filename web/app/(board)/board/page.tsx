"use client";

// The queue board (doc 03 §6, doc 04 §3). Its single job: show the token being
// served in each room, in numerals a family reads from 8 metres across a noisy
// OPD hall. The deliberate aesthetic risk (doc 04 §5) is the train-platform
// board — huge marigold numerals on deep clinic green, a soft flip when they
// change, and a two-language spoken announcement with a chime.
//
// Three elements, in order of importance: (1) the now-serving numeral per room,
// (2) the next-three tokens, (3) the wait range. Everything else stays quiet.

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle } from "lucide-react";
import { Board, BoardDept, fetchBoard } from "@/app/_lib/queue";
import { QueueEvent, useQueueSocket } from "@/app/_lib/useQueueSocket";

export default function BoardPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [clock, setClock] = useState("");
  // Remember what each room was serving so we announce only real changes.
  const lastServed = useRef<Map<string, number | null>>(new Map());
  const primed = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchBoard();
      setBoard(next);
      announceChanges(next, lastServed.current, primed.current);
      primed.current = true;
    } catch {
      /* leave the last good board up; the socket will nudge another refresh */
    }
  }, []);

  const onEvent = useCallback(
    (e: QueueEvent) => {
      if (e.type === "queue_update") void refresh();
      if (e.type === "downtime") {
        setBoard((b) => (b && b.downtime !== e.active ? { ...b, downtime: e.active } : b));
      }
    },
    [refresh],
  );
  const { connected } = useQueueSocket({ onEvent });

  useEffect(() => {
    void refresh();
    // A slow safety poll in case a socket frame is missed (doc 01 §5 heartbeat).
    const poll = setInterval(() => void refresh(), 20_000);
    const tick = setInterval(() => setClock(nowString()), 1000);
    setClock(nowString());
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [refresh]);

  const depts = board?.departments ?? [];

  return (
    <main className="board">
      {/* Injected raw so SSR and client agree byte-for-byte — a `<style>{text}`
          child hydrates as a mismatch (quotes in the CSS escape differently). */}
      <style dangerouslySetInnerHTML={{ __html: BOARD_CSS }} />
      {board?.downtime && (
        <div className="downtime" role="status">
          <span className="dot" /> OFFLINE — tokens continue · टोकन जारी हैं
        </div>
      )}
      <header className="topbar">
        <div className="brand">
          <span className="mark"><Activity aria-hidden="true" /></span> Dhara OPD Queue
        </div>
        <div className="meta">
          <span className={`live ${connected ? "on" : "off"}`}>
            <span className="pulse" /> {connected ? "LIVE" : "RECONNECTING"}
          </span>
          <span className="clock">{clock}</span>
        </div>
      </header>

      {depts.length === 0 ? (
        <div className="empty">
          <div className="empty-num">—</div>
          <p>No tokens in the queue yet.</p>
        </div>
      ) : (
        <section className="grid" data-count={depts.length}>
          {depts.map((d) => (
            <RoomCard key={d.department_key} dept={d} />
          ))}
        </section>
      )}
    </main>
  );
}

function RoomCard({ dept }: { dept: BoardDept }) {
  const urgent = dept.now_serving_reason != null;
  return (
    <article className={`room ${urgent ? "room-urgent" : ""}`}>
      {/* The room line names the department *and* the doctor running it: a family
          reading "Room 3" from across the hall still has to ask someone which
          door that is (S-UX.6). */}
      <div className="room-head">
        <div className="room-id">
          <h2>{dept.department_name}</h2>
          {dept.doctor_name && <p className="room-doc">{dept.doctor_name}</p>}
        </div>
        <span className="waiting">{dept.waiting_count} waiting</span>
      </div>

      <div className="serving-label">NOW SERVING</div>
      <div key={dept.now_serving ?? "none"} className="serving-num">
        {dept.now_serving ?? "—"}
      </div>
      {/* The name under the numeral, because the numeral alone is what people
          mishear across a noisy hall. Nothing clinical is shown next to it — the
          board never carries a complaint or a red-flag reason (doc 03 §6). */}
      {dept.now_serving_name && (
        <div className="serving-name" data-testid="serving-name">
          {dept.now_serving_name}
        </div>
      )}
      {dept.now_serving_reason && (
        <div className="urgent-chip">
          <AlertTriangle aria-hidden="true" /> Priority assistance
        </div>
      )}

      <div className="next-row">
        <span className="next-label">NEXT</span>
        <div className="next-tokens">
          {dept.next.length === 0 && <span className="next-empty">—</span>}
          {dept.next.map((e) => (
            <span
              key={e.token_no}
              className={`chip ${e.priority === "urgent" ? "chip-urgent" : ""}`}
              aria-label={`Token ${e.token_no}${e.priority === "urgent" ? ", priority assistance" : ""}`}
            >
              <b>{e.token_no}</b>
              {e.patient_name && <i>{e.patient_name}</i>}
              {e.red_flag && <AlertTriangle className="flag" aria-hidden="true" />}
            </span>
          ))}
        </div>
      </div>

      <div className="wait">
        {dept.waiting_count > 0 ? (
          <>
            ~{dept.est_wait_low}–{dept.est_wait_high} min
          </>
        ) : (
          <>Queue clear</>
        )}
      </div>
    </article>
  );
}

// -- announcements ------------------------------------------------------------

function announceChanges(board: Board, last: Map<string, number | null>, primed: boolean) {
  for (const d of board.departments) {
    const prev = last.get(d.department_key);
    last.set(d.department_key, d.now_serving);
    // Skip the first paint (don't read out the whole board on load) and no-ops.
    if (!primed || prev === d.now_serving || d.now_serving == null) continue;
    announce(d.now_serving, d.department_name);
  }
  // Track rooms that dropped off so a re-appearance re-announces.
  for (const key of Array.from(last.keys())) {
    if (!board.departments.some((d) => d.department_key === key)) last.delete(key);
  }
}

let audioCtx: AudioContext | null = null;

function announce(token: number, room: string) {
  if (typeof window === "undefined") return;
  chime();
  const speak = window.speechSynthesis;
  if (!speak) return;
  // Two languages, back to back (doc 03 §6): English then Hindi.
  const en = new SpeechSynthesisUtterance(`Token ${token}, ${room}. Please proceed.`);
  en.lang = "en-IN";
  en.rate = 0.95;
  const hi = new SpeechSynthesisUtterance(`टोकन ${token}, ${room}. कृपया अंदर आइए।`);
  hi.lang = "hi-IN";
  hi.rate = 0.95;
  try {
    speak.speak(en);
    speak.speak(hi);
  } catch {
    /* speech unavailable (headless / no voices) — the numeral still flips */
  }
}

function chime() {
  try {
    audioCtx =
      audioCtx ?? new (window.AudioContext || (window as unknown as AudioWin).webkitAudioContext)();
    const ctx = audioCtx;
    const now = ctx.currentTime;
    // A gentle two-note station chime (G5 → C6).
    for (const [i, freq] of [784, 1047].entries()) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const t = now + i * 0.18;
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(0.18, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.35);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.4);
    }
  } catch {
    /* no audio context (headless) */
  }
}

type AudioWin = { webkitAudioContext: typeof AudioContext };

function nowString(): string {
  return new Date().toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

// -- style (train-board) ------------------------------------------------------

const BOARD_CSS = `
.board {
  min-height: 100vh;
  background: #092f29;
  color: #fff;
  padding: clamp(16px, 2.4vw, 40px);
  font-feature-settings: "tnum" 1;
  overflow: hidden;
}
.downtime {
  position: sticky; top: 0; z-index: 5;
  display: flex; align-items: center; justify-content: center; gap: 12px;
  background: var(--accent); color: #3a2606;
  font-weight: 800; letter-spacing: .06em; text-transform: uppercase;
  padding: 12px; border-radius: 14px; margin-bottom: 18px;
  font-size: clamp(15px, 1.6vw, 22px);
  box-shadow: 0 8px 30px rgba(226,144,31,.35);
}
.downtime .dot { width: 12px; height: 12px; border-radius: 50%;
  background: #3a2606; animation: blink 1.1s steps(1) infinite; }
@keyframes blink { 50% { opacity: .2; } }
.topbar {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: clamp(12px, 2vw, 28px);
}
.brand { font-size: clamp(18px, 2vw, 30px); font-weight: 800; letter-spacing: .01em;
  display: flex; align-items: center; gap: 12px; color: #dff3ec; }
.brand .mark { width: 34px; height: 34px; display: grid; place-items: center;
  border-radius: 7px; background: rgba(255,255,255,.09); color: #71d8ba; }
.brand .mark svg { width: 20px; height: 20px; }
.meta { display: flex; align-items: center; gap: clamp(14px, 2vw, 30px); }
.live { display: inline-flex; align-items: center; gap: 8px; font-weight: 700;
  letter-spacing: .12em; font-size: clamp(12px, 1.1vw, 15px); }
.live.on { color: #7fe0c4; } .live.off { color: var(--accent); }
.live .pulse { width: 10px; height: 10px; border-radius: 50%; background: currentColor;
  box-shadow: 0 0 0 0 currentColor; animation: pulse 1.6s infinite; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(127,224,196,.6);} 70%{box-shadow:0 0 0 12px rgba(127,224,196,0);} 100%{box-shadow:0 0 0 0 rgba(127,224,196,0);} }
.clock { font-size: clamp(18px, 1.8vw, 28px); font-weight: 700; color: #cdeae0;
  font-variant-numeric: tabular-nums; }
.grid {
  display: grid; gap: clamp(12px, 1.6vw, 24px);
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr));
}
.grid[data-count="1"] { grid-template-columns: minmax(0, 720px); justify-content: center; }
.room {
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 24px;
  padding: clamp(16px, 1.8vw, 30px);
  display: flex; flex-direction: column;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.06);
}
.room-urgent { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(226,144,31,.35) inset; }
.room-head { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
.room-id { min-width: 0; }
.room-head h2 { margin: 0; font-size: clamp(20px, 1.7vw, 30px); font-weight: 700; color: #eafaf4; }
.room-doc { margin: 2px 0 0; font-size: clamp(13px, 1.1vw, 18px); font-weight: 600; color: #8fd8c1; }
.waiting { color: #9fc3b8; font-size: clamp(12px, 1vw, 16px); font-weight: 600; white-space: nowrap; }
.serving-label { margin-top: 8px; color: #8fb6ab; letter-spacing: .22em;
  font-size: clamp(11px, .9vw, 14px); font-weight: 700; }
.serving-num {
  font-weight: 800; line-height: .92; color: var(--accent);
  font-size: clamp(96px, 12vw, 200px);
  text-shadow: 0 6px 30px rgba(226,144,31,.35);
  animation: flip .5s var(--ease);
}
@keyframes flip {
  0% { transform: translateY(-14%) scale(.96); opacity: 0; filter: blur(2px); }
  100% { transform: none; opacity: 1; filter: none; }
}
.serving-name { margin-top: 2px; font-size: clamp(18px, 2vw, 34px); font-weight: 700;
  color: #eafaf4; line-height: 1.2; overflow-wrap: anywhere; }
.urgent-chip {
  align-self: flex-start; display: inline-flex; align-items: center; gap: 7px; margin-top: 6px;
  background: var(--accent); color: #3a2606; font-weight: 800;
  padding: 6px 12px; border-radius: 6px; font-size: clamp(12px, 1vw, 16px);
}
.urgent-chip svg { width: 1em; height: 1em; }
.next-row { display: flex; align-items: center; gap: 14px; margin-top: auto; padding-top: 18px; }
.next-label { color: #8fb6ab; letter-spacing: .2em; font-size: clamp(11px,.9vw,13px); font-weight: 700; }
.next-tokens { display: flex; gap: 10px; flex-wrap: wrap; }
.chip {
  display: inline-flex; align-items: baseline; gap: 8px;
  background: rgba(255,255,255,.1); color: #eafaf4;
  border-radius: 12px; padding: 8px 14px; font-weight: 800;
  font-size: clamp(20px, 1.9vw, 30px); font-variant-numeric: tabular-nums;
}
.chip b { font-weight: 800; }
.chip i { font-style: normal; font-weight: 600; font-size: .55em; color: #cfe8df;
  max-width: 12ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chip-urgent { background: var(--accent); color: #3a2606; }
.chip .flag { width: .7em; height: .7em; }
.next-empty { color: #6f9389; font-size: 24px; }
.wait { margin-top: 14px; color: #bfe0d6; font-weight: 700;
  font-size: clamp(14px, 1.2vw, 20px); font-variant-numeric: tabular-nums; }
.empty { height: 70vh; display: flex; flex-direction: column; align-items: center;
  justify-content: center; color: #9fc3b8; gap: 8px; }
.empty-num { font-size: clamp(80px, 14vw, 200px); font-weight: 800; color: rgba(255,255,255,.12); }
@media (prefers-reduced-motion: reduce) {
  .serving-num, .downtime .dot, .live .pulse { animation: none; }
}
`;
