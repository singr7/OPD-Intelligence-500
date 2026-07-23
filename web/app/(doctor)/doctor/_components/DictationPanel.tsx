"use client";

// The dictation panel (doc 03 §7, doc 04 §3/§5).
//
// Single job: let the doctor confirm in one pass that what the record says is
// what they said — and make a drug they did not say impossible to sign by
// accident.
//
// The three elements, in order:
//   1. the flagged drugs, each shown against the doctor's own words;
//   2. the rest of the prescription;
//   3. the transcript, advice and follow-up, quiet.
//
// The deliberate risk for this surface (doc 04 §5) is the **provenance line**:
// every mapped value hangs under the phrase it came from, joined by a hairline.
// The diff a doctor needs is not "form v1 against form v2" — every review UI
// does that — it is "speech against record". When those two disagree, the
// hairline turns danger-red and the two lines physically stop lining up. That is
// the entire safety argument of S10 made visible in one glance.
//
// Everything else stays quiet: no cards, no shadows, no colour except the two
// that already mean something in this console (danger, marigold).

import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError } from "@/app/_lib/queue";
import type { Dictation, MappedFields, Med } from "../_lib/dictation";
import {
  correct,
  fetchDictation,
  mapFields,
  signDictation,
  startDictation,
  transcribeAudio,
} from "../_lib/dictation";

type Props = {
  token: string;
  visitId: string;
  patientName: string;
  onClose: () => void;
  onSigned?: () => void;
};

// Chrome ships this prefixed; Firefox does not ship it at all. Typed here
// rather than pulled from a DOM lib because it is not in the standard one.
type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
};

type SpeechCtor = new () => SpeechRecognitionLike;

function speechCtor(): SpeechCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: SpeechCtor; webkitSpeechRecognition?: SpeechCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function DictationPanel({ token, visitId, patientName, onClose, onSigned }: Props) {
  const [dictation, setDictation] = useState<Dictation | null>(null);
  const [transcript, setTranscript] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const speechRef = useRef<SpeechRecognitionLike | null>(null);
  const startedAt = useRef<number>(0);

  const signed = dictation?.status === "signed";
  const fields = dictation?.fields ?? null;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const existing = await fetchDictation(token, visitId);
        if (cancelled) return;
        setDictation(existing);
        setTranscript(existing?.transcript ?? "");
      } catch (err) {
        if (!(err instanceof AuthError)) setError("Could not open the consult note.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, visitId]);

  const run = useCallback(async (label: string, fn: () => Promise<Dictation>) => {
    setBusy(label);
    setError(null);
    try {
      setDictation(await fn());
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work.");
      return false;
    } finally {
      setBusy(null);
    }
  }, []);

  // -- capture ---------------------------------------------------------------

  const stopRecording = useCallback(() => {
    speechRef.current?.stop();
    speechRef.current = null;
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  }, []);

  const startRecording = useCallback(async () => {
    setError(null);
    startedAt.current = Date.now();

    // Web Speech is the fast path: text appears as the doctor talks. It is also
    // the one that ships their voice to a cloud recogniser, so the recording
    // below runs alongside it — on a V-OSS box the server pass is local Whisper
    // and strictly better, and it is the only path at all in Firefox.
    const Ctor = speechCtor();
    if (Ctor) {
      const rec = new Ctor();
      rec.lang = "en-IN";
      rec.continuous = true;
      rec.interimResults = false;
      rec.onresult = (e) => {
        let text = "";
        for (let i = 0; i < e.results.length; i += 1) text += `${e.results[i][0].transcript} `;
        setTranscript(text.trim());
      };
      rec.onerror = () => setError("Live transcription stopped — the recording is still running.");
      rec.onend = () => setRecording(false);
      speechRef.current = rec;
      rec.start();
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const seconds = (Date.now() - startedAt.current) / 1000;
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        // Only fall back to the server when Web Speech produced nothing: no
        // point paying for a second transcription of the same audio.
        if (!Ctor || !transcript.trim()) {
          setBusy("transcribing");
          try {
            const out = await transcribeAudio(token, blob, seconds);
            setTranscript(out.text);
            if (out.uncertain) setError("That recording was hard to hear — please read it through.");
          } catch (err) {
            setError(err instanceof Error ? err.message : "Could not transcribe that.");
          } finally {
            setBusy(null);
          }
        }
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      if (!Ctor) setError("No microphone available — type the note instead.");
      else setRecording(true);
    }
  }, [token, transcript]);

  useEffect(() => () => stopRecording(), [stopRecording]);

  // -- verbs -----------------------------------------------------------------

  const onMap = useCallback(async () => {
    if (!transcript.trim()) return;
    const saved = await run("saving", () => startDictation(token, visitId, transcript));
    if (!saved) return;
    const current = await fetchDictation(token, visitId);
    if (!current) return;
    await run("mapping", () => mapFields(token, current.id));
  }, [run, token, visitId, transcript]);

  const patch = useCallback(
    async (next: Partial<MappedFields>) => {
      if (!dictation) return;
      await run("saving", () => correct(token, dictation.id, next));
    },
    [run, token, dictation],
  );

  const acknowledge = useCallback(
    (index: number) => {
      if (!fields) return;
      const meds = fields.meds.map((m, i) => (i === index ? { ...m, acknowledged: true } : m));
      void patch({ meds });
    },
    [fields, patch],
  );

  const editMed = useCallback(
    (index: number, key: keyof Med, value: string) => {
      if (!fields) return;
      const meds = fields.meds.map((m, i) =>
        // A name the doctor retypes loses its acknowledgement: it is a different
        // drug now and has to earn its verdict again from the server.
        i === index
          ? { ...m, [key]: value, ...(key === "name" ? { acknowledged: false } : {}) }
          : m,
      );
      void patch({ meds });
    },
    [fields, patch],
  );

  const onSign = useCallback(async () => {
    if (!dictation) return;
    const ok = await run("signing", () => signDictation(token, dictation.id));
    if (ok) onSigned?.();
  }, [run, token, dictation, onSigned]);

  // -- render ----------------------------------------------------------------

  const blocking = dictation?.blocking_meds ?? [];
  const flagged = (fields?.meds ?? [])
    .map((med, index) => ({ med, index }))
    .filter(({ med }) => !med.known || med.unsaid);
  const clean = (fields?.meds ?? [])
    .map((med, index) => ({ med, index }))
    .filter(({ med }) => med.known && !med.unsaid);

  return (
    <section className="dict" aria-label={`Consult note for ${patientName}`}>
      <header className="dict-h">
        <div>
          <h2>Consult note</h2>
          <p className="dict-sub">
            {patientName}
            {dictation?.prompt_ref && !signed && (
              <span className="dict-model"> · mapped by {dictation.model}</span>
            )}
          </p>
        </div>
        <button className="dict-close" onClick={onClose} aria-label="Close the consult note">
          Close
        </button>
      </header>

      {error && <p className="dict-err">{error}</p>}

      {signed && (
        <p className="dict-signed">
          Signed{dictation?.signed_at ? ` at ${new Date(dictation.signed_at).toLocaleTimeString()}` : ""}.
          This note is locked.
        </p>
      )}

      {/* 3 (but first in time): capture. Shrinks to a quiet strip once mapped. */}
      {!signed && (
        <div className={`dict-capture${fields ? " is-done" : ""}`}>
          <div className="dict-caprow">
            <button
              className={`dict-mic${recording ? " is-rec" : ""}`}
              onClick={recording ? stopRecording : startRecording}
              disabled={!!busy}
            >
              <span className="dict-dot" aria-hidden="true" />
              {recording ? "Stop" : fields ? "Re-dictate" : "Dictate"}
            </button>
            <button
              className="dict-map"
              onClick={onMap}
              disabled={!!busy || !transcript.trim() || recording}
            >
              {busy === "mapping" ? "Mapping…" : fields ? "Map again" : "Map to fields"}
            </button>
            {busy === "transcribing" && <span className="dict-busy">Transcribing…</span>}
          </div>
          <textarea
            className="dict-transcript"
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder="Dictate, or type the note here. Hinglish is fine."
            rows={fields ? 2 : 5}
            aria-label="Dictation transcript"
          />
        </div>
      )}

      {dictation?.mapping_error && !fields && (
        <p className="dict-err">
          The mapping model is unreachable. The note above is saved — try again when it is back.
        </p>
      )}

      {fields && (
        <div className="dict-review">
          {/* 1. the flagged drugs */}
          {flagged.length > 0 && (
            <div className="dict-flagged">
              <h3>
                {flagged.length} {flagged.length === 1 ? "drug needs" : "drugs need"} your eyes
              </h3>
              {flagged.map(({ med, index }) => (
                <MedRow
                  key={`${med.name}-${index}`}
                  med={med}
                  locked={signed}
                  onAcknowledge={() => acknowledge(index)}
                  onEdit={(key, value) => editMed(index, key, value)}
                />
              ))}
            </div>
          )}

          {/* 2. the rest of the prescription */}
          {clean.length > 0 && (
            <div className="dict-clean">
              <h3>Prescription</h3>
              {clean.map(({ med, index }) => (
                <MedRow
                  key={`${med.name}-${index}`}
                  med={med}
                  locked={signed}
                  onAcknowledge={() => acknowledge(index)}
                  onEdit={(key, value) => editMed(index, key, value)}
                />
              ))}
            </div>
          )}
          {fields.meds.length === 0 && <p className="dict-nomeds">No medicines in this note.</p>}

          {/* No provenance line here: the impression is drawn from the whole
              note, so quoting "what it came from" reprints the transcript that
              is already on screen a few centimetres above. A provenance line
              that is always the same text stops being read, and then it stops
              being read on the rows where it matters. */}
          <Provenance label="Impression" spoken="" written={fields.diagnosis ?? "—"} />

          {fields.treatment_events.map((ev, i) => (
            <Provenance
              key={i}
              label="Treatment"
              spoken={ev.as_spoken}
              written={[
                ev.regimen,
                ev.cycle != null ? `cycle ${ev.cycle}` : "",
                ev.next_due ? `next due ${ev.next_due}` : "",
              ]
                .filter(Boolean)
                .join(" · ")}
            />
          ))}

          {(fields.follow_up.when || fields.follow_up.instructions) && (
            <Provenance
              label="Follow-up"
              spoken={fields.follow_up.as_spoken}
              written={[fields.follow_up.when, fields.follow_up.instructions]
                .filter(Boolean)
                .join(" · ")}
            />
          )}

          {fields.advice.length > 0 && (
            <div className="dict-advice">
              <h3>Advice</h3>
              <ul>
                {fields.advice.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            </div>
          )}

          {fields.unclear.length > 0 && (
            <p className="dict-unclear">
              Not heard clearly: {fields.unclear.join("; ")}. Nothing was filled in for these.
            </p>
          )}

          {!signed && (
            <div className="dict-signbar">
              <button className="dict-sign" onClick={onSign} disabled={!!busy || blocking.length > 0}>
                {busy === "signing" ? "Signing…" : "Sign this note"}
              </button>
              {blocking.length > 0 && (
                <p className="dict-block">
                  {blocking.length} flagged {blocking.length === 1 ? "drug" : "drugs"} still
                  unconfirmed: {blocking.join(", ")}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/** One drug: the written line, and underneath it what the doctor actually said. */
function MedRow({
  med,
  locked,
  onAcknowledge,
  onEdit,
}: {
  med: Med;
  locked: boolean;
  onAcknowledge: () => void;
  onEdit: (key: keyof Med, value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(med.name);
  const flagged = !med.known || med.unsaid;
  const state = med.acknowledged ? "ack" : flagged ? "flag" : "ok";

  const commit = () => {
    setEditing(false);
    if (draft.trim() && draft !== med.name) onEdit("name", draft.trim());
    else setDraft(med.name);
  };

  return (
    <div className={`med med-${state}`}>
      <div className="med-line">
        {editing && !locked ? (
          <input
            className="med-input"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === "Enter" && commit()}
            aria-label="Drug name"
          />
        ) : (
          <button
            className="med-name"
            onClick={() => !locked && setEditing(true)}
            disabled={locked}
            title={locked ? undefined : "Tap to fix"}
          >
            {med.name}
          </button>
        )}
        <span className="med-sig">
          {[med.dose, med.route, med.freq, med.duration].filter(Boolean).join(" · ") || "—"}
        </span>
        {med.generic && med.generic.toLowerCase() !== med.name.toLowerCase() && (
          <span className="med-generic">{med.generic}</span>
        )}
      </div>

      {/* the provenance hairline: what was said, under what was written */}
      <div className="med-spoken">
        <span className="med-tick" aria-hidden="true" />
        <span className="med-heard">
          {med.as_spoken ? `“${med.as_spoken}”` : "not traceable to anything in the transcript"}
        </span>
      </div>

      {flagged && (
        <div className="med-why">
          {med.unsaid && (
            <p className="med-alert">
              <strong>You are not recorded as saying this name.</strong> It may have been corrected
              or invented on the way in — check it against your own words above.
            </p>
          )}
          {!med.known && !med.ambiguous && (
            <p className="med-alert">
              <strong>Not on the hospital formulary.</strong> Nothing has been changed for you.
            </p>
          )}
          {med.ambiguous && (
            <p className="med-alert">
              <strong>This name is close to more than one drug.</strong> Nothing has been chosen —
              please type the one you meant.
            </p>
          )}
          {med.suggestions?.length > 0 && (
            <p className="med-sugg">
              Close to:{" "}
              {med.suggestions.map((s) => (
                <span key={s.name} className="med-cand">
                  {s.name} <em>({s.generic})</em>
                </span>
              ))}
            </p>
          )}
          {!locked && !med.acknowledged && (
            <button className="med-confirm" onClick={onAcknowledge}>
              Yes, I meant this — keep it
            </button>
          )}
          {med.acknowledged && <p className="med-acked">Confirmed by you. Still off-formulary.</p>}
        </div>
      )}
    </div>
  );
}

/** The same provenance idea at lower volume, for the non-drug fields. */
function Provenance({
  label,
  spoken,
  written,
}: {
  label: string;
  spoken: string;
  written: string;
}) {
  return (
    <div className="prov">
      <span className="prov-label">{label}</span>
      <div className="prov-body">
        <span className="prov-written">{written}</span>
        {spoken && <span className="prov-spoken">“{spoken}”</span>}
      </div>
    </div>
  );
}
