"use client";

// The doctor console (doc 03 §5, doc 04 §3; rebuilt for S-UX.6, rescoped in
// Session B).
//
// Its single job: absorb one patient's story in twenty seconds and move the
// queue with one key. Layout is two columns — the rail of who is waiting, and
// the stage for who is in front of you.
//
// Session B changed two things about that stage.
//
// **The rail now has three scopes.** `Mine` is the default, which is only a sane
// default because AR3 made the kiosk assign essentially every arrival. The
// safety net is `Unassigned`, and its count stays visible whether or not its tab
// is open — it is what a kiosk `Skip` and every offline arrival land in, and a
// patient nobody's list contains is a patient nobody sees.
//
// **The context spine never unmounts.** Identity, token, diagnosis, allergies
// and red flags used to live at the top of the patient card, which was replaced
// wholesale by the dictation panel the moment the doctor pressed D. So the
// allergy the doctor most needed while prescribing was the one thing guaranteed
// to be off screen. Now the spine sits above the tab row and survives every tab,
// including Consult.
//
// The queue verbs are still the S8 queue's (`callNext` / `setEntryState`),
// imported rather than reimplemented: same state machine, same audit trail, same
// order the board and the coordinator see. Every mutation refetches the day,
// because the coordinator may be moving the same line at the same time.

import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError, callNext, setEntryState } from "@/app/_lib/queue";
import { patientDocuments, verifyDocument, type MedicalDocument } from "@/app/_lib/records";
import type { Day, DayRow, DayScope, PatientCard as Card, RxMode } from "../_lib/doctor";
import { concludeVisit, fetchDay, fetchPatient, takePatient } from "../_lib/doctor";
import { clearToken, getToken, setToken } from "../_lib/session";
import { ConcludeDialog } from "./ConcludeDialog";
import { CONSOLE_CSS, DICTATION_CSS, REPORTS_CSS } from "./consoleStyles";
import { ContextSpine } from "./ContextSpine";
import { DayRail } from "./DayRail";
import { DictationPanel } from "./DictationPanel";
import { EncounterBar, type Action } from "./EncounterBar";
import { Login } from "./Login";
import { PatientCard } from "./PatientCard";
import { ReportsTab } from "./ReportsTab";
import { WorkTabs, type WorkTab } from "./WorkTabs";

export function Console() {
  const [token, setTok] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [day, setDay] = useState<Day | null>(null);
  const [scope, setScope] = useState<DayScope>("mine");
  const [card, setCard] = useState<Card | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which tab of the record is open. The consult note is one of them rather than
  // a panel that replaces the screen: by the time the doctor dictates they have
  // finished reading, but they must not lose the red flags to do it.
  const [tab, setTab] = useState<WorkTab>("overview");
  // Which visits got a signed note. Seeded from the card (`note_signed`), so a
  // reload no longer forgets, and added to the moment this console watches a
  // signature land — the card is not refetched at that instant.
  const [signedNotes, setSignedNotes] = useState<Set<string>>(new Set());
  // The conclusion dialog. Open only for an ending that loses something, or
  // when the console does not know a note was signed.
  const [concluding, setConcluding] = useState(false);
  const [concludeError, setConcludeError] = useState<string | null>(null);
  // The patient's scanned papers (MRD2). Fetched alongside the card rather than
  // when the Reports tab is opened, because the spine states the count before
  // the doctor opens anything — that is the module's entire stated intent: know
  // what the papers say before the patient is in the room.
  const [documents, setDocuments] = useState<MedicalDocument[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  const scopeRef = useRef<DayScope>("mine");
  /** Whose reports the console is currently meant to be showing. */
  const patientRef = useRef<string | null>(null);

  useEffect(() => {
    setTok(getToken());
    setReady(true);
  }, []);

  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  useEffect(() => {
    scopeRef.current = scope;
  }, [scope]);

  const signOut = useCallback(() => {
    clearToken();
    setTok(null);
    setDay(null);
    setCard(null);
    setSelected(null);
    setTab("overview");
    setDocuments([]);
  }, []);

  /**
   * This patient's scanned papers.
   *
   * Guarded by the patient it was asked for: the card and the documents are two
   * fetches, and a doctor moving down the rail faster than the network would
   * otherwise land patient A's reports under patient B's name. On a screen whose
   * whole job is "these are this patient's lab values", that is the one bug that
   * must not be possible.
   */
  const loadDocuments = useCallback(async (tok: string, patientId: string) => {
    setDocumentsLoading(true);
    setDocumentsError(null);
    try {
      const rows = await patientDocuments(tok, patientId);
      if (patientRef.current !== patientId) return;
      setDocuments(rows);
    } catch {
      if (patientRef.current !== patientId) return;
      setDocuments([]);
      setDocumentsError("Could not load this patient's scanned reports.");
    } finally {
      if (patientRef.current === patientId) setDocumentsLoading(false);
    }
  }, []);

  const loadDay = useCallback(
    async (tok: string, want?: DayScope) => {
      try {
        const next = await fetchDay(tok, want ?? scopeRef.current);
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
        const next = await fetchPatient(tok, visitId);
        setCard(next);
        // The record, not this session's memory, is what says a note is signed.
        if (next.note_signed) setSignedNotes((prev) => new Set(prev).add(next.visit_id));
        setError(null);
        if (patientRef.current !== next.patient_id) {
          patientRef.current = next.patient_id;
          setDocuments([]);
        }
        await loadDocuments(tok, next.patient_id);
      } catch (err) {
        if (err instanceof AuthError) signOut();
        else setError("Could not open that patient.");
      }
    },
    [signOut, loadDocuments],
  );

  /**
   * "I have read this against the pages."
   *
   * Recorded against the *reading*, not the document — a re-extraction produces
   * different numbers and clears it, because carrying a doctor's name onto
   * numbers they never saw is the worst thing this module could do.
   */
  const onVerify = useCallback(
    async (documentId: string) => {
      if (!token || verifying) return;
      setVerifying(documentId);
      try {
        const updated = await verifyDocument(token, documentId);
        setDocuments((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not record that review.");
      } finally {
        setVerifying(null);
      }
    },
    [token, verifying],
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

  // Switching scope refetches but deliberately leaves the stage alone. A doctor
  // checking the unassigned pool mid-consult has not stopped consulting, and
  // clearing the card under them would be the console changing the subject.
  const onScope = useCallback(
    async (next: DayScope) => {
      if (!token || next === scope) return;
      setScope(next);
      scopeRef.current = next;
      await loadDay(token, next);
    },
    [token, scope, loadDay],
  );

  const onTake = useCallback(
    async (row: DayRow) => {
      if (!token || busy) return;
      setBusy(true);
      try {
        await takePatient(token, row.visit_id);
        await loadDay(token);
        // Taking a patient is an act of intent — open them. The doctor said
        // "I'll see this one", and the next thing they want is the card.
        await openPatient(token, row.visit_id);
        setTab("overview");
      } catch (err) {
        if (err instanceof AuthError) signOut();
        else setError(err instanceof Error ? err.message : "Could not take that patient.");
      } finally {
        setBusy(false);
      }
    },
    [token, busy, loadDay, openPatient, signOut],
  );

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

  // After anything that may have moved this patient out of the worklist: fall
  // through to whoever is now in the room, or clear the stage.
  const resettle = useCallback(
    async (tok: string, visitId: string) => {
      const next = await loadDay(tok);
      const still = next?.rows.some((r) => r.visit_id === visitId);
      if (still) {
        await openPatient(tok, visitId);
        return;
      }
      const inRoom = next?.rows.find((r) => r.state === "in_consult" || r.state === "called");
      if (inRoom) await openPatient(tok, inRoom.visit_id);
      else {
        setCard(null);
        setSelected(null);
      }
    },
    [loadDay, openPatient],
  );

  /**
   * Ending the consult, on the record (plan §5.3b).
   *
   * With a signed note this is one tap and nothing is lost, so it does not ask:
   * a confirmation on the ordinary ending would train doctors to click through
   * the dialog that exists for the endings that *do* lose something. Without
   * one, the dialog opens and names what will not exist afterwards.
   */
  const onConclude = useCallback(
    async (mode: RxMode, note: string) => {
      if (!token || !card || busy) return;
      setBusy(true);
      setConcludeError(null);
      try {
        await concludeVisit(token, card.visit_id, mode, note);
        setConcluding(false);
        await resettle(token, card.visit_id);
      } catch (err) {
        if (err instanceof AuthError) signOut();
        else setConcludeError(err instanceof Error ? err.message : "That was refused.");
      } finally {
        setBusy(false);
      }
    },
    [token, card, busy, resettle, signOut],
  );

  const onAction = useCallback(
    async (action: Action) => {
      if (!token || !card?.entry_id || busy) return;
      // Completing a consult is now a conclusion, not a bare queue transition:
      // a visit that simply stops cannot be told apart from one the doctor was
      // interrupted in the middle of.
      if (action === "done") {
        if (signedNotes.has(card.visit_id)) {
          await onConclude("system", "");
        } else {
          setConcludeError(null);
          setConcluding(true);
        }
        return;
      }
      setBusy(true);
      try {
        await setEntryState(token, card.entry_id, action);
        await resettle(token, card.visit_id);
      } catch (err) {
        if (err instanceof AuthError) signOut();
        else setError(err instanceof Error ? err.message : "That action was refused.");
      } finally {
        setBusy(false);
      }
    },
    [token, card, busy, signedNotes, onConclude, resettle, signOut],
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
        if (selectedRef.current) {
          setTab((open) => (open === "consult" ? "overview" : "consult"));
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [token, onCallNext]);

  // Moving to *another* patient returns to the Overview — the note is that
  // visit's, not a scratchpad that follows the doctor down the list. Keyed on
  // the value changing rather than on the effect running, because the first load
  // resolves `selected` asynchronously: a plain dependency effect closes a note
  // the doctor opened a moment earlier, and it does it often enough to look
  // random.
  const tabFor = useRef<string | null>(null);
  useEffect(() => {
    if (tabFor.current !== null && tabFor.current !== selected) setTab("overview");
    tabFor.current = selected;
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

  const isMine = card ? card.assigned_doctor_id === day?.doctor_id : true;

  return (
    <div className="console">
      <style dangerouslySetInnerHTML={{ __html: CONSOLE_CSS + DICTATION_CSS + REPORTS_CSS }} />

      {/* The app bar carries identity only. Every verb that moves the queue
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
            <b>{day?.counts.waiting ?? 0}</b> waiting
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
            onScope={onScope}
            onTake={onTake}
            busy={busy}
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
            onDictate={() => setTab("consult")}
            noteSigned={card ? signedNotes.has(card.visit_id) : false}
          />

          {card ? (
            <>
              {/* Sticky, and mounted for every tab including Consult. */}
              <ContextSpine
                card={card}
                isMine={isMine}
                documents={documents}
                documentsLoading={documentsLoading}
                onOpenReports={() => setTab("reports")}
              />

              <WorkTabs
                tab={tab}
                onTab={setTab}
                answerCount={card.answers.length}
                noteSigned={signedNotes.has(card.visit_id)}
                reportCount={documents.length}
                reportsUnverified={documents.some((d) => d.extraction && !d.extraction.verified)}
              />

              {tab === "reports" ? (
                <ReportsTab
                  token={token}
                  documents={documents}
                  loading={documentsLoading}
                  error={documentsError}
                  verifying={verifying}
                  onVerify={onVerify}
                />
              ) : tab === "consult" ? (
                <DictationPanel
                  token={token}
                  visitId={card.visit_id}
                  patientName={card.name}
                  patientMrn={card.mrn}
                  visitDate={card.visit_date}
                  doctorName={day?.doctor_name ?? "Doctor"}
                  departmentName={card.department_name}
                  onClose={() => setTab("overview")}
                  onSigned={() => setSignedNotes((prev) => new Set(prev).add(card.visit_id))}
                  onConclude={() => {
                    setConcludeError(null);
                    setConcluding(true);
                  }}
                />
              ) : (
                <PatientCard card={card} tab={tab} />
              )}
            </>
          ) : (
            <p className="empty-state">
              {day && day.rows.length > 0
                ? "Pick a patient from the list, or press N to call the next one."
                : "Nobody is waiting yet."}
            </p>
          )}
        </section>
      </main>

      {concluding && card && (
        <ConcludeDialog
          patientName={card.name}
          noteSigned={signedNotes.has(card.visit_id) || card.note_signed}
          busy={busy}
          error={concludeError}
          onConfirm={onConclude}
          onCancel={() => setConcluding(false)}
        />
      )}
    </div>
  );
}
