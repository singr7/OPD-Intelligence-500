"use client";

// The doctor console (doc 03 §5, doc 04 §3).
//
// Its single job: absorb one patient's story in twenty seconds and move the
// queue with one key. Layout is therefore two columns and nothing else — the
// rail of who is waiting, and the card of who is in front of you.
//
// The action verbs are the S8 queue's (`callNext` / `setEntryState`), imported
// rather than reimplemented: same state machine, same audit trail, same order
// the board and the coordinator see. Every mutation refetches the day, because
// the coordinator may be moving the same line at the same time.

import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError, callNext, setEntryState } from "@/app/_lib/queue";
import type { Day, DayRow, PatientCard as Card } from "../_lib/doctor";
import { fetchDay, fetchPatient } from "../_lib/doctor";
import { clearToken, getToken, setToken } from "../_lib/session";
import { CONSOLE_CSS } from "./consoleStyles";
import { DayRail } from "./DayRail";
import { Login } from "./Login";
import { PatientCard } from "./PatientCard";

type Action = "in_consult" | "done" | "no_show" | "lab_requeue";

export function Console() {
  const [token, setTok] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [day, setDay] = useState<Day | null>(null);
  const [card, setCard] = useState<Card | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dictationNote, setDictationNote] = useState(false);
  const selectedRef = useRef<string | null>(null);

  useEffect(() => {
    setTok(getToken());
    setReady(true);
  }, []);

  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  const signOut = useCallback(() => {
    clearToken();
    setTok(null);
    setDay(null);
    setCard(null);
    setSelected(null);
  }, []);

  const loadDay = useCallback(
    async (tok: string) => {
      try {
        const next = await fetchDay(tok);
        setDay(next);
        setError(null);
        return next;
      } catch (err) {
        if (err instanceof AuthError) signOut();
        else setError("Could not load today's list.");
        return null;
      }
    },
    [signOut],
  );

  const openPatient = useCallback(
    async (tok: string, visitId: string) => {
      setSelected(visitId);
      try {
        setCard(await fetchPatient(tok, visitId));
        setError(null);
      } catch (err) {
        if (err instanceof AuthError) signOut();
        else setError("Could not open that patient.");
      }
    },
    [signOut],
  );

  // First load: the day, and whoever is already in the room.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      const next = await loadDay(token);
      if (cancelled || !next || next.rows.length === 0) return;
      const inRoom = next.rows.find((r) => r.state === "in_consult" || r.state === "called");
      await openPatient(token, (inRoom ?? next.rows[0]).visit_id);
    })();
    return () => {
      cancelled = true;
    };
  }, [token, loadDay, openPatient]);

  const onCallNext = useCallback(async () => {
    if (!token || !day || busy) return;
    setBusy(true);
    try {
      await callNext(token, day.department_key);
      const next = await loadDay(token);
      const inRoom = next?.rows.find((r) => r.state === "called" || r.state === "in_consult");
      if (inRoom) await openPatient(token, inRoom.visit_id);
    } catch (err) {
      if (err instanceof AuthError) signOut();
      else setError(err instanceof Error ? err.message : "Could not call the next patient.");
    } finally {
      setBusy(false);
    }
  }, [token, day, busy, loadDay, openPatient, signOut]);

  const onAction = useCallback(
    async (action: Action) => {
      if (!token || !card?.entry_id || busy) return;
      setBusy(true);
      try {
        await setEntryState(token, card.entry_id, action);
        const next = await loadDay(token);
        // A patient who has left the worklist should not stay on screen: fall
        // through to whoever is now in the room, or clear the card.
        const still = next?.rows.some((r) => r.visit_id === card.visit_id);
        if (still) await openPatient(token, card.visit_id);
        else {
          const inRoom = next?.rows.find((r) => r.state === "in_consult" || r.state === "called");
          if (inRoom) await openPatient(token, inRoom.visit_id);
          else {
            setCard(null);
            setSelected(null);
          }
        }
      } catch (err) {
        if (err instanceof AuthError) signOut();
        else setError(err instanceof Error ? err.message : "That action was refused.");
      } finally {
        setBusy(false);
      }
    },
    [token, card, busy, loadDay, openPatient, signOut],
  );

  // Keyboard shortcuts (doc 04 §3: N = next patient, D = dictate). Ignored while
  // a field has focus, so typing a phone number never calls a patient.
  useEffect(() => {
    if (!token) return;
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) {
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === "n") {
        e.preventDefault();
        void onCallNext();
      } else if (key === "d") {
        e.preventDefault();
        setDictationNote(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [token, onCallNext]);

  useEffect(() => {
    if (!dictationNote) return;
    const t = setTimeout(() => setDictationNote(false), 3200);
    return () => clearTimeout(t);
  }, [dictationNote]);

  if (!ready) return null;
  if (!token) {
    return (
      <Login
        onToken={(t) => {
          setToken(t);
          setTok(t);
        }}
      />
    );
  }

  return (
    <div className="console">
      <style dangerouslySetInnerHTML={{ __html: CONSOLE_CSS }} />

      <header className="appbar">
        <div className="appbar-l">
          <strong>{day?.doctor_name ?? "Doctor"}</strong>
          <span className="room">{day?.department_name ?? ""}</span>
        </div>
        <div className="appbar-r">
          <kbd className="hint">N</kbd>
          <button className="callnext" onClick={onCallNext} disabled={busy || !day}>
            Call next patient
          </button>
          <button className="signout" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>

      {error && <p className="err-toast">{error}</p>}
      {dictationNote && (
        <p className="note-toast">Dictation arrives in S10 — the shortcut is wired and waiting.</p>
      )}

      <main className="split">
        {day ? (
          <DayRail
            day={day}
            selectedVisitId={selected}
            onSelect={(row: DayRow) => token && openPatient(token, row.visit_id)}
          />
        ) : (
          <p className="loading">Loading today&rsquo;s list…</p>
        )}

        <section className="stage">
          {card ? (
            <PatientCard card={card} busy={busy} onAction={onAction} />
          ) : (
            <p className="empty-state">
              {day && day.rows.length > 0
                ? "Pick a patient from the list, or press N to call the next one."
                : "Nobody is waiting yet."}
            </p>
          )}
        </section>
      </main>
    </div>
  );
}
