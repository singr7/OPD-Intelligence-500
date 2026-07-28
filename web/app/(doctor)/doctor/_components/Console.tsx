"use client";

// The doctor console (doc 03 §5, doc 04 §3; rebuilt for S-UX.6).
//
// Its single job: absorb one patient's story in twenty seconds and move the
// queue with one key. Layout is therefore two columns and nothing else — the
// rail of who is waiting, and the card of who is in front of you.
//
// What S-UX.6 changed is *where the encounter lives*. It used to be implied by a
// small state chip on the card and acted on by buttons scattered between the app
// bar and the bottom of the card, which made "is this consult finished?" a
// question the doctor had to reconstruct. Now one `EncounterBar` sits above the
// card, says the state in words, and carries exactly one filled next step.
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
import { CONSOLE_CSS, DICTATION_CSS } from "./consoleStyles";
import { DayRail } from "./DayRail";
import { DictationPanel } from "./DictationPanel";
import { EncounterBar, type Action } from "./EncounterBar";
import { Login } from "./Login";
import { PatientCard } from "./PatientCard";

export function Console() {
  const [token, setTok] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [day, setDay] = useState<Day | null>(null);
  const [card, setCard] = useState<Card | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The consult note takes the stage rather than floating over it: by the time
  // the doctor dictates they have finished reading the card, and a modal over a
  // clinical summary invites signing a note while half-reading the patient.
  const [dictating, setDictating] = useState(false);
  // Which visits got a signed note in this session. The card model does not carry
  // note status, and asking the server per row would be a request per patient —
  // so the console remembers what it watched happen, and says nothing about
  // visits it did not (the bar then reads "Write consult note", which is safe:
  // the panel itself shows the real signed state when opened).
  const [signedNotes, setSignedNotes] = useState<Set<string>>(new Set());
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
    setDictating(false);
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
      // Only auto-open if the doctor has not already picked someone. The day
      // fetch resolves after the page is interactive, and a load that yanks the
      // stage away from the patient they just tapped is worse than a slow one.
      if (selectedRef.current) return;
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
        // Only with a patient on the stage: a consult note belongs to a visit.
        setDictating((open) => (selectedRef.current ? !open : open));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [token, onCallNext]);

  // Moving to *another* patient closes the note — it is that visit's, not a
  // scratchpad that follows the doctor down the list. Keyed on the value
  // changing rather than on the effect running, because the first load resolves
  // `selected` asynchronously: a plain dependency effect closes a note the
  // doctor opened a moment earlier, and it does it often enough to look random.
  const dictatingFor = useRef<string | null>(null);
  useEffect(() => {
    if (dictatingFor.current !== null && dictatingFor.current !== selected) {
      setDictating(false);
    }
    dictatingFor.current = selected;
  }, [selected]);

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
      <style dangerouslySetInnerHTML={{ __html: CONSOLE_CSS + DICTATION_CSS }} />

      {/* The app bar carries identity only. Every verb that moves the queue now
          lives on the encounter bar, next to the patient it acts on. */}
      <header className="appbar">
        <div className="appbar-l">
          <strong>{day?.doctor_name ?? "Doctor"}</strong>
          <span className="room">
            {day?.department_name ?? ""}
            {day?.date ? ` · ${day.date}` : ""}
          </span>
        </div>
        <div className="appbar-r">
          <span className="appbar-count">
            <b>{day?.rows.filter((r) => r.state === "waiting").length ?? 0}</b> waiting
          </span>
          <button className="signout" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>

      {error && <p className="err-toast">{error}</p>}

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
          <EncounterBar
            card={card}
            day={day}
            busy={busy}
            onAction={onAction}
            onCallNext={onCallNext}
            onDictate={() => setDictating(true)}
            noteSigned={card ? signedNotes.has(card.visit_id) : false}
          />

          {card && dictating ? (
            <DictationPanel
              token={token}
              visitId={card.visit_id}
              patientName={card.name}
              patientMrn={card.mrn}
              visitDate={card.visit_date}
              doctorName={day?.doctor_name ?? "Doctor"}
              departmentName={card.department_name}
              onClose={() => setDictating(false)}
              onSigned={() =>
                setSignedNotes((prev) => new Set(prev).add(card.visit_id))
              }
            />
          ) : card ? (
            <PatientCard card={card} />
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
