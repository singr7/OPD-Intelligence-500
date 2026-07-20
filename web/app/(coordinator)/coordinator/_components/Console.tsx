"use client";

// The coordinator console (doc 03 §6, doc 04 §3). Pragmatic tables are allowed
// here, but the same tokens as everywhere; the one strong move is that downtime
// flips the whole app bar to marigold with an unmissable "OFFLINE — tokens
// continue" banner. Three jobs: keep the queue moving (call/next/reorder), enter
// & exit downtime, and reconcile what happened while the system was dark.

import { useCallback, useEffect, useState } from "react";
import {
  AuthError,
  callNext,
  Console as ConsoleData,
  ConsoleDept,
  ConsoleEntry,
  fetchConsole,
  reorder,
  setDowntime,
  setEntryState,
} from "@/app/_lib/queue";
import { QueueEvent, useQueueSocket } from "@/app/_lib/useQueueSocket";
import { ReconciliationTab } from "./ReconciliationTab";
import { PaperEntryTab } from "./PaperEntryTab";
import { PrintTab } from "./PrintTab";
import { CONSOLE_CSS } from "./consoleStyles";

type Tab = "queue" | "reconciliation" | "paper" | "print";

export function Console({ token, onSignOut }: { token: string; onSignOut: () => void }) {
  const [data, setData] = useState<ConsoleData | null>(null);
  const [tab, setTab] = useState<Tab>("queue");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await fetchConsole(token));
    } catch (e) {
      if (e instanceof AuthError) onSignOut();
    }
  }, [token, onSignOut]);

  const onEvent = useCallback(
    (e: QueueEvent) => {
      if (e.type === "queue_update") void refresh();
      if (e.type === "downtime")
        // Only re-render when it actually flipped — a reconnect re-sends the
        // current flag, and a new object each time would churn the DOM.
        setData((d) => (d && d.downtime !== e.active ? { ...d, downtime: e.active } : d));
    },
    [refresh],
  );
  useQueueSocket({ onEvent });

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const guard = async (fn: () => Promise<void>) => {
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      if (e instanceof AuthError) return onSignOut();
      setError(e instanceof Error ? e.message : "Something went wrong");
    }
  };

  const downtime = data?.downtime ?? false;

  return (
    <main className={`console ${downtime ? "is-downtime" : ""}`}>
      {/* Raw-injected so SSR/client hydration agrees (quotes in CSS escape
          differently as a text child). */}
      <style dangerouslySetInnerHTML={{ __html: CONSOLE_CSS }} />

      <header className="appbar">
        <div className="appbar-l">
          <span className="logo">◐</span>
          <strong>Coordinator</strong>
          <nav className="tabs">
            {(["queue", "reconciliation", "paper", "print"] as Tab[]).map((t) => (
              <button
                key={t}
                className={tab === t ? "tab on" : "tab"}
                onClick={() => setTab(t)}
              >
                {TAB_LABELS[t]}
              </button>
            ))}
          </nav>
        </div>
        <div className="appbar-r">
          <button
            className={`downtime-toggle ${downtime ? "active" : ""}`}
            onClick={() => guard(() => setDowntime(token, !downtime))}
          >
            {downtime ? "Exit downtime" : "Enter downtime"}
          </button>
          <button className="signout" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </header>

      {downtime && (
        <div className="downtime-banner" role="status">
          OFFLINE — tokens continue · टोकन जारी हैं · Kiosks keep issuing from paper blocks.
        </div>
      )}

      {error && <div className="err-toast">{error}</div>}

      {tab === "queue" && (
        <QueueTab data={data} token={token} onAction={guard} />
      )}
      {tab === "reconciliation" && <ReconciliationTab token={token} onSignOut={onSignOut} />}
      {tab === "paper" && (
        <PaperEntryTab token={token} departments={deptOptions(data)} onDone={refresh} />
      )}
      {tab === "print" && <PrintTab token={token} />}
    </main>
  );
}

const TAB_LABELS: Record<Tab, string> = {
  queue: "Queue",
  reconciliation: "Reconciliation",
  paper: "Paper entry",
  print: "Print sheets",
};

function deptOptions(data: ConsoleData | null): { key: string; name: string }[] {
  return (data?.departments ?? []).map((d) => ({
    key: d.department_key,
    name: d.department_name,
  }));
}

// -- queue tab ----------------------------------------------------------------

function QueueTab({
  data,
  token,
  onAction,
}: {
  data: ConsoleData | null;
  token: string;
  onAction: (fn: () => Promise<void>) => Promise<void>;
}) {
  if (!data) return <p className="loading">Loading queue…</p>;
  const active = data.departments.filter((d) => d.entries.length > 0);
  if (active.length === 0) {
    return <div className="empty-state">No one is in the queue right now.</div>;
  }
  return (
    <div className="queue-grid">
      {active.map((dept) => (
        <DeptQueue key={dept.department_key} dept={dept} token={token} onAction={onAction} />
      ))}
    </div>
  );
}

function DeptQueue({
  dept,
  token,
  onAction,
}: {
  dept: ConsoleDept;
  token: string;
  onAction: (fn: () => Promise<void>) => Promise<void>;
}) {
  const [dragId, setDragId] = useState<string | null>(null);

  const move = (index: number, dir: -1 | 1) => {
    const ids = dept.entries.map((e) => e.id);
    const j = index + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[index], ids[j]] = [ids[j], ids[index]];
    void onAction(() => reorder(token, dept.department_key, ids));
  };

  const drop = (targetId: string) => {
    if (!dragId || dragId === targetId) return;
    const ids = dept.entries.map((e) => e.id);
    const from = ids.indexOf(dragId);
    const to = ids.indexOf(targetId);
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    setDragId(null);
    void onAction(() => reorder(token, dept.department_key, ids));
  };

  const waiting = dept.entries.filter((e) => e.state === "waiting").length;

  return (
    <section className="dept">
      <div className="dept-head">
        <h2>{dept.department_name}</h2>
        <button
          className="call-next"
          onClick={() => onAction(() => callNext(token, dept.department_key))}
        >
          Call next ▸
        </button>
      </div>
      <div className="dept-sub">{waiting} waiting</div>

      <ul className="entries">
        {dept.entries.map((entry, i) => (
          <li
            key={entry.id}
            className={`entry state-${entry.state} ${entry.priority === "urgent" ? "urgent" : ""}`}
            draggable={entry.state === "waiting"}
            onDragStart={() => setDragId(entry.id)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => drop(entry.id)}
          >
            <div className="tok-col">
              <span className="drag" aria-hidden>
                ⠿
              </span>
              <span className="tok">{entry.token_no}</span>
            </div>

            <div className="mid">
              <div className="chips">
                <StateBadge state={entry.state} />
                {entry.priority === "urgent" && (
                  <span className="chip-urgent">
                    ⚠ Urgent{entry.priority_reason ? ` · ${entry.priority_reason}` : ""}
                  </span>
                )}
                {entry.red_flag_count > 0 && (
                  <span className="chip-flag">{entry.red_flag_count} red flag{entry.red_flag_count > 1 ? "s" : ""}</span>
                )}
              </div>
              <div className="chief">{entry.chief_complaint || "—"}</div>
            </div>

            <div className="actions">
              <EntryActions entry={entry} token={token} onAction={onAction} />
              {entry.state === "waiting" && (
                <div className="nudge">
                  <button onClick={() => move(i, -1)} aria-label="Move up">
                    ↑
                  </button>
                  <button onClick={() => move(i, 1)} aria-label="Move down">
                    ↓
                  </button>
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function EntryActions({
  entry,
  token,
  onAction,
}: {
  entry: ConsoleEntry;
  token: string;
  onAction: (fn: () => Promise<void>) => Promise<void>;
}) {
  const to = (state: ConsoleEntry["state"]) => () =>
    onAction(() => setEntryState(token, entry.id, state));

  switch (entry.state) {
    case "waiting":
      return (
        <>
          <button className="act primary" onClick={to("called")}>
            Call
          </button>
          <button className="act ghost" onClick={to("no_show")}>
            No-show
          </button>
        </>
      );
    case "called":
      return (
        <>
          <button className="act primary" onClick={to("in_consult")}>
            Start
          </button>
          <button className="act ghost" onClick={to("no_show")}>
            No-show
          </button>
        </>
      );
    case "in_consult":
      return (
        <>
          <button className="act primary" onClick={to("done")}>
            Done
          </button>
          <button className="act ghost" onClick={to("lab_requeue")}>
            To lab
          </button>
        </>
      );
    case "lab_requeue":
      return (
        <button className="act primary" onClick={to("waiting")}>
          Back to queue
        </button>
      );
    default:
      return null;
  }
}

function StateBadge({ state }: { state: ConsoleEntry["state"] }) {
  const label: Record<ConsoleEntry["state"], string> = {
    waiting: "Waiting",
    called: "Called",
    in_consult: "In consult",
    done: "Done",
    no_show: "No-show",
    lab_requeue: "At lab",
  };
  return <span className={`state-badge s-${state}`}>{label[state]}</span>;
}
