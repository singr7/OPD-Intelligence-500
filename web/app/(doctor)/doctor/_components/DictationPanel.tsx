"use client";

// The consult note (doc 03 §7, doc 04 §3/§5; the four-step flow is plan §5.2).
//
// Single job: let the doctor confirm in one pass that what the record says is
// what they meant — and make a drug they did not say impossible to sign by
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
// hairline turns danger-red and the two lines physically stop lining up.
//
// Session C changed four things about how this surface is reached and left.
//
// **Speech is an input method, not a prerequisite.** "Type note" opens the same
// fields with no model in the loop (`compose`), and everything downstream — the
// corrections trail, the formulary refusal, the signature, the prescription —
// is byte-for-byte the path a dictated note takes. There is no second
// prescription writer, because a parallel writer around the signature boundary
// is how drug-safety validation gets bypassed two quarters from now.
//
// **A failed mapping is a state you can walk out of.** The banner says the
// recording is safe, the fields below it are open, and the signature is still
// reachable. The model being down is not a reason a patient leaves without a
// prescription.
//
// **The recording state stops making claims it cannot back.** The bars are
// samples from a live analyser node on the actual stream; when there is no
// analyser there are no bars, only an elapsed timer and an indicator. An evenly
// spaced decorative waveform is a false claim that audio is being captured.
//
// **`Stop & transcribe` is green.** Red is for clinical danger and destruction.
// Stopping in order to transcribe is the expected safe progression, and
// painting it red trains the doctor to ignore red where it matters.

import { ChevronDown, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError } from "@/app/_lib/queue";
import type { Assessment, Dictation, MappedFields, Med } from "../_lib/dictation";
import { EMPTY_ASSESSMENT } from "../_lib/dictation";
import { RxPanel } from "./RxPanel";
import {
  composeNote,
  correct,
  fetchDictation,
  mapFields,
  signDictation,
  startDictation,
} from "../_lib/dictation";
import { clock, useVoiceCapture } from "../_lib/useVoiceCapture";

type Props = {
  token: string;
  visitId: string;
  patientName: string;
  patientMrn: string;
  visitDate: string;
  doctorName: string;
  departmentName: string;
  /** doc 24 §6: whether treatment here is given in numbered cycles. When false
   *  the regimen/cycle provenance lines go — not because they would be empty,
   *  but because a "Treatment: cycle 4 · AC-T" line under an ayurveda consult is
   *  a claim about a record that does not exist. */
  showsRegimenEvents: boolean;
  /** The prakriti / vikriti / agni / koshtha / nidana fields (doc 24 §6.1).
   *  Structured note fields on the existing record — same `fields`/`edits`
   *  trail, same signature, no new note type and no new table. */
  ayurvedaAssessment: boolean;
  /** The diet & lifestyle section of the Rx composer and the print (§6.2). */
  pathyaApathya: boolean;
  onClose: () => void;
  onSigned?: () => void;
  /** Opens the conclusion dialog. Owned by the console, because concluding ends
   *  the encounter and moves the queue — it is not a note-level act. */
  onConclude: () => void;
};

/** The five assessment lines, in the order an ayurveda physician records them:
 *  constitution, then what is wrong in *this* illness, then the digestive and
 *  bowel findings that decide the treatment, then what is driving it.
 *
 *  Labels carry the familiar English word beside the Sanskrit — doc 24 §5's tone
 *  rule, where the term appears and the everyday word does the work. The
 *  placeholders are examples rather than instructions, because the honest answer
 *  is often "vata-pitta, pitta rising" and a field that implies a single word
 *  would make a doctor round to one. */
const ASSESSMENT_FIELDS: {
  key: keyof Assessment;
  label: string;
  placeholder: string;
}[] = [
  { key: "prakriti", label: "Prakriti (constitution)", placeholder: "e.g. vata-pitta" },
  { key: "vikriti", label: "Vikriti (dosha involvement)", placeholder: "e.g. pitta vriddhi" },
  { key: "agni", label: "Agni (digestion)", placeholder: "e.g. tikshna / manda / sama" },
  { key: "koshtha", label: "Koshtha (bowel tendency)", placeholder: "e.g. krura / mridu / madhya" },
  { key: "nidana", label: "Nidana (aggravating factors)", placeholder: "What is driving it" },
];

export function DictationPanel({
  token,
  visitId,
  patientName,
  patientMrn,
  visitDate,
  doctorName,
  departmentName,
  showsRegimenEvents,
  ayurvedaAssessment,
  pathyaApathya,
  onClose,
  onSigned,
  onConclude,
}: Props) {
  const [dictation, setDictation] = useState<Dictation | null>(null);
  const [transcript, setTranscript] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

  const signed = dictation?.status === "signed";
  const fields = dictation?.fields ?? null;
  const mappingFailed = !!dictation?.mapping_error && !dictation?.mapped;
  // No transcript at all: this note was typed, and the provenance lines have to
  // say so rather than reporting a missing transcript as a failure to trace.
  const typedNote = !!dictation && !(dictation.transcript ?? "").trim();

  // True once the doctor has touched the transcript for this visit. The panel
  // renders immediately and loads the stored note a moment later, so without
  // this a doctor who starts typing the instant the note opens has the first
  // sentence silently replaced when the fetch lands. Losing dictated words is
  // the one failure this whole surface exists to prevent, so the server only
  // seeds a field nobody has written in.
  const touched = useRef(false);
  useEffect(() => {
    touched.current = false;
  }, [visitId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const existing = await fetchDictation(token, visitId);
        if (cancelled) return;
        setDictation(existing);
        if (!touched.current) setTranscript(existing?.transcript ?? "");
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

  // The recorder itself moved to `useVoiceCapture` in M4, when the ambient note
  // became the second surface with one. Same Web Speech fast path, same
  // `MediaRecorder` alongside it, same analyser-driven meter, and the same rule:
  // no bars without an analyser.
  const {
    recording,
    elapsed,
    levels,
    transcribing,
    start: startRecording,
    stop: stopRecording,
  } = useVoiceCapture({
    token,
    endpoint: "/dictation/stt",
    currentTranscript: transcript,
    onTranscript: (text) => {
      touched.current = true;
      setTranscript(text);
    },
    onError: setError,
  });

  // -- verbs -----------------------------------------------------------------

  const onMap = useCallback(async () => {
    if (!transcript.trim()) return;
    const saved = await run("saving", () => startDictation(token, visitId, transcript));
    if (!saved) return;
    const current = await fetchDictation(token, visitId);
    if (!current) return;
    await run("mapping", () => mapFields(token, current.id));
  }, [run, token, visitId, transcript]);

  /** "Type note": the same record, with no model anywhere near it. */
  const onTypeNote = useCallback(async () => {
    const saved = await run("saving", () => startDictation(token, visitId, transcript));
    if (!saved) return;
    const current = await fetchDictation(token, visitId);
    if (!current) return;
    await run("opening", () => composeNote(token, current.id));
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

  const removeMed = useCallback(
    (index: number) => {
      if (!fields) return;
      void patch({ meds: fields.meds.filter((_, i) => i !== index) });
    },
    [fields, patch],
  );

  const addMed = useCallback(
    (med: { name: string; dose: string; freq: string; duration: string }) => {
      if (!fields) return;
      void patch({
        meds: [
          ...fields.meds,
          {
            name: med.name,
            dose: med.dose || null,
            route: null,
            freq: med.freq || null,
            duration: med.duration || null,
          },
        ] as unknown as Med[],
      });
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

  const step: Step = signed ? "prescription" : fields ? "review" : "capture";

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

      <StepRail step={step} signed={signed} />

      {error && (
        <p className="dict-err" role="alert">
          {error}
        </p>
      )}

      {signed && (
        <p className="dict-signed">
          Signed{dictation?.signed_at ? ` at ${new Date(dictation.signed_at).toLocaleTimeString()}` : ""}.
          This note is locked.
        </p>
      )}

      {/* ④ doc 03 §8: what the signature produced, in the same column as the act. */}
      {signed && (
        <RxPanel
          token={token}
          visitId={visitId}
          signedAt={dictation?.signed_at ?? null}
          patientName={patientName}
          patientMrn={patientMrn}
          visitDate={visitDate}
          doctorName={doctorName}
          departmentName={departmentName}
          onAuthError={onClose}
        />
      )}

      {/* ① capture. Shrinks to a quiet strip once the fields exist. */}
      {!signed && (
        <div className={`dict-capture${fields ? " is-done" : ""}`}>
          <div className="dict-caprow">
            <button
              className={`dict-dictate${recording ? " is-rec" : ""}`}
              onClick={recording ? stopRecording : startRecording}
              disabled={!!busy}
              data-testid="dictate"
            >
              <span className="dict-dot" aria-hidden="true" />
              {recording ? "Stop & transcribe" : fields ? "Re-dictate" : "Dictate"}
            </button>

            {!fields && !recording && (
              <button
                className="dict-type"
                onClick={onTypeNote}
                disabled={!!busy}
                data-testid="type-note"
              >
                {busy === "opening" ? "Opening…" : "Type note"}
              </button>
            )}

            {fields && (
              <button
                className="dict-map"
                onClick={onMap}
                disabled={!!busy || !transcript.trim() || recording}
              >
                {busy === "mapping" ? "Mapping…" : "Map again"}
              </button>
            )}
            {!fields && !recording && transcript.trim() && (
              <button className="dict-map" onClick={onMap} disabled={!!busy}>
                {busy === "mapping" ? "Mapping…" : "Map to fields"}
              </button>
            )}

            {transcribing && <span className="dict-busy">Transcribing…</span>}

            {/* The escape hatch lives in the overflow, not in marigold at the
                top of the screen. It is legitimate and it is not the default. */}
            <div className="dict-more">
              <button
                className="dict-more-btn"
                aria-expanded={moreOpen}
                aria-haspopup="menu"
                onClick={() => setMoreOpen((v) => !v)}
                title="Other ways to end this consult"
                data-testid="consult-more"
              >
                More <ChevronDown aria-hidden="true" />
              </button>
              {moreOpen && (
                <div className="dict-menu" role="menu">
                  <button
                    role="menuitem"
                    onClick={() => {
                      setMoreOpen(false);
                      onConclude();
                    }}
                    data-testid="conclude-without-note"
                  >
                    Conclude without a system note
                    <small>For a paper script, or a consult that prescribes nothing.</small>
                  </button>
                </div>
              )}
            </div>
          </div>

          {recording && (
            <div className="dict-rec" role="status">
              <span className="dict-rec-live">Recording</span>
              <time className="dict-rec-time">{clock(elapsed)}</time>
              {levels.length > 0 ? (
                <span className="dict-meter" aria-hidden="true">
                  {levels.map((lv, i) => (
                    <i key={i} style={{ height: `${Math.max(8, Math.round(lv * 100))}%` }} />
                  ))}
                </span>
              ) : (
                <span className="dict-meter-off">no level meter on this browser</span>
              )}
            </div>
          )}

          <textarea
            className="dict-transcript"
            value={transcript}
            onChange={(e) => {
              touched.current = true;
              setTranscript(e.target.value);
            }}
            placeholder="Dictate, or type the note here. Hinglish is fine."
            rows={fields ? 2 : 5}
            aria-label="Dictation transcript"
          />
        </div>
      )}

      {/* A failed mapping is recoverable, and says so in those words. */}
      {mappingFailed && !signed && (
        <p className="dict-recover" data-testid="mapping-failed">
          <strong>We could not structure this note.</strong> Your recording is saved. Fill the
          fields below and continue — signing works exactly the same way.
        </p>
      )}

      {/* ② review */}
      {fields && (
        <div className="dict-review">
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
                  typed={typedNote}
                  onAcknowledge={() => acknowledge(index)}
                  onEdit={(key, value) => editMed(index, key, value)}
                  onDelete={() => removeMed(index)}
                />
              ))}
            </div>
          )}

          {clean.length > 0 && (
            <div className="dict-clean">
              <h3>Prescription</h3>
              {clean.map(({ med, index }) => (
                <MedRow
                  key={`${med.name}-${index}`}
                  med={med}
                  locked={signed}
                  typed={typedNote}
                  onAcknowledge={() => acknowledge(index)}
                  onEdit={(key, value) => editMed(index, key, value)}
                  onDelete={() => removeMed(index)}
                />
              ))}
            </div>
          )}
          {fields.meds.length === 0 && (
            <p className="dict-nomeds">
              No medicines in this note. A consult that ends in advice and a follow-up date is a
              complete consult.
            </p>
          )}

          {!signed && <AddMed onAdd={addMed} busy={!!busy} />}

          {/* No provenance line on the impression: it is drawn from the whole
              note, so quoting "what it came from" reprints the transcript that
              is already on screen a few centimetres above. A provenance line
              that is always the same text stops being read, and then it stops
              being read on the rows where it matters. */}
          <EditableField
            label="Impression"
            value={fields.diagnosis ?? ""}
            spoken=""
            locked={signed}
            placeholder="Diagnosis or impression"
            onCommit={(v) => patch({ diagnosis: v || null })}
          />

          {showsRegimenEvents &&
            fields.treatment_events.map((ev, i) => (
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

          {/* The doctor's own assessment (doc 24 §6.1). Above the advice and
              below the impression, which is the order it is thought in. Nothing
              here is ever machine-written: no model produces these fields, and
              the ayurveda prompts are told in as many words not to infer a
              prakriti. So every one is `spoken=""` — there is no "as spoken"
              because nobody spoke it, and a provenance quote under a field the
              doctor typed would be a claim about a transcript. */}
          {ayurvedaAssessment &&
            ASSESSMENT_FIELDS.map(({ key, label, placeholder }) => (
              <EditableField
                key={key}
                label={label}
                value={(fields.assessment ?? EMPTY_ASSESSMENT)[key]}
                spoken=""
                locked={signed}
                placeholder={placeholder}
                onCommit={(v) =>
                  patch({
                    assessment: { ...(fields.assessment ?? EMPTY_ASSESSMENT), [key]: v },
                  })
                }
              />
            ))}

          <EditableField
            label="Follow-up"
            value={fields.follow_up.when ?? ""}
            spoken={fields.follow_up.as_spoken}
            locked={signed}
            placeholder="When to come back"
            onCommit={(v) =>
              patch({ follow_up: { ...fields.follow_up, when: v || null } })
            }
          />

          <EditableField
            label="Advice"
            value={fields.advice.join("\n")}
            spoken=""
            locked={signed}
            multiline
            placeholder="One line per piece of advice"
            onCommit={(v) =>
              patch({ advice: v.split("\n").map((s) => s.trim()).filter(Boolean) })
            }
          />

          {/* Diet & lifestyle (doc 24 §6.2). Its own field rather than more
              `advice` lines, because it prints under its own heading on both
              copies — and because in an ayurveda consult this is half of the
              treatment, not a footnote to it. */}
          {pathyaApathya && (
            <EditableField
              label="Pathya – Apathya"
              value={(fields.pathya_apathya ?? []).join("\n")}
              spoken=""
              locked={signed}
              multiline
              placeholder="One line each — what to favour, what to avoid"
              onCommit={(v) =>
                patch({
                  pathya_apathya: v
                    .split("\n")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
            />
          )}

          {fields.unclear.length > 0 && (
            <p className="dict-unclear">
              Not heard clearly: {fields.unclear.join("; ")}. Nothing was filled in for these.
            </p>
          )}

          {/* ③ sign. The prescription does not exist until this happens, which
              is why there is nothing to print above it. */}
          {!signed && (
            <div className="dict-signbar">
              <button
                className="dict-sign"
                onClick={onSign}
                disabled={!!busy || blocking.length > 0}
                data-testid="sign-note"
              >
                {busy === "signing" ? "Signing…" : "Sign this note"}
              </button>
              {blocking.length > 0 && (
                <p className="dict-block">
                  {blocking.length} flagged {blocking.length === 1 ? "drug" : "drugs"} still
                  unconfirmed: {blocking.join(", ")}
                </p>
              )}
              <p className="dict-preprint" data-testid="pre-print-note">
                The prescription is produced by the signature. Nothing can be printed or sent
                before it.
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// -- the step rail ------------------------------------------------------------

type Step = "capture" | "review" | "sign" | "prescription";

const STEPS: { id: Step; label: string }[] = [
  { id: "capture", label: "Capture" },
  { id: "review", label: "Review" },
  { id: "sign", label: "Sign" },
  { id: "prescription", label: "Prescription" },
];

/**
 * Where the doctor is in the consult, stated rather than implied.
 *
 * An indicator, not navigation: the steps are produced by the record's state
 * and cannot be jumped to. Status is text plus position, never colour alone
 * (doc 14 principle 4), so the current step is also named in the label.
 */
function StepRail({ step, signed }: { step: Step; signed: boolean }) {
  const reached = STEPS.findIndex((s) => s.id === step);
  return (
    <ol className="dict-steps" aria-label="Consult progress">
      {STEPS.map((s, i) => {
        const state = signed && s.id === "sign" ? "done" : i < reached ? "done" : i === reached ? "now" : "todo";
        return (
          <li key={s.id} className={`dstep is-${state}`} data-testid={`step-${s.id}`}>
            <span className="dstep-n" aria-hidden="true">
              {i + 1}
            </span>
            <span className="dstep-l">
              {s.label}
              {state === "now" && <em> — you are here</em>}
              {state === "done" && <em> — done</em>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// -- one drug -----------------------------------------------------------------

/** One drug: the written line, and underneath it what the doctor actually said. */
function MedRow({
  med,
  locked,
  typed,
  onAcknowledge,
  onEdit,
  onDelete,
}: {
  med: Med;
  locked: boolean;
  /** Nothing was ever dictated on this note, so there is no transcript for a
   *  line to be traceable *to*. Saying "not traceable" here would read as a
   *  warning about a drug the doctor typed with their own hands. */
  typed: boolean;
  onAcknowledge: () => void;
  onEdit: (key: keyof Med, value: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(med.name);
  const [confirmDelete, setConfirmDelete] = useState(false);
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

        {locked ? (
          <span className="med-sig">
            {[med.dose, med.route, med.freq, med.duration].filter(Boolean).join(" · ") || "—"}
          </span>
        ) : (
          <span className="med-sig-edit">
            <SigField label="Dose" value={med.dose} onCommit={(v) => onEdit("dose", v)} />
            <SigField label="Route" value={med.route} onCommit={(v) => onEdit("route", v)} />
            <SigField label="Frequency" value={med.freq} onCommit={(v) => onEdit("freq", v)} />
            <SigField label="Duration" value={med.duration} onCommit={(v) => onEdit("duration", v)} />
          </span>
        )}

        {med.generic && med.generic.toLowerCase() !== med.name.toLowerCase() && (
          <span className="med-generic">{med.generic}</span>
        )}

        {/* Named, tooltipped, and confirmed. Removing a drug line is the one
            edit on this row that cannot be seen afterwards by reading it. */}
        {!locked && !confirmDelete && (
          <button
            className="med-del"
            onClick={() => setConfirmDelete(true)}
            aria-label={`Remove ${med.name} from this note`}
            title={`Remove ${med.name}`}
          >
            <Trash2 aria-hidden="true" />
          </button>
        )}
        {!locked && confirmDelete && (
          <span className="med-delconfirm">
            Remove {med.name}?
            <button onClick={onDelete} data-testid="confirm-remove-med">
              Remove
            </button>
            <button onClick={() => setConfirmDelete(false)}>Keep</button>
          </span>
        )}
      </div>

      {/* the provenance hairline: what was said, under what was written */}
      <div className="med-spoken">
        <span className="med-tick" aria-hidden="true" />
        <span className="med-heard">
          {med.as_spoken
            ? `“${med.as_spoken}”`
            : typed
              ? "typed by you — nothing was dictated on this note"
              : "not traceable to anything in the transcript"}
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

/** One part of the signature line — dose, route, frequency, duration. */
function SigField({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: string | null;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  return (
    <input
      className="med-sigin"
      value={draft}
      placeholder={label}
      aria-label={label}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft !== (value ?? "") && onCommit(draft.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

/** Adding a line by hand — the other half of what "Type note" needs to exist.
 *  A name is required, because a nameless drug line is not recoverable by a
 *  doctor scanning a prescription, and the server drops it anyway. */
function AddMed({
  onAdd,
  busy,
}: {
  onAdd: (med: { name: string; dose: string; freq: string; duration: string }) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [dose, setDose] = useState("");
  const [freq, setFreq] = useState("");
  const [duration, setDuration] = useState("");

  if (!open) {
    return (
      <button className="med-add" onClick={() => setOpen(true)} data-testid="add-med">
        <Plus aria-hidden="true" /> Add a medicine
      </button>
    );
  }

  const submit = () => {
    if (!name.trim()) return;
    onAdd({ name: name.trim(), dose, freq, duration });
    setName("");
    setDose("");
    setFreq("");
    setDuration("");
    setOpen(false);
  };

  return (
    <div className="med-addform">
      <input
        value={name}
        autoFocus
        placeholder="Medicine name"
        aria-label="Medicine name"
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        data-testid="add-med-name"
      />
      <input value={dose} placeholder="Dose" aria-label="Dose" onChange={(e) => setDose(e.target.value)} />
      <input
        value={freq}
        placeholder="Frequency"
        aria-label="Frequency"
        onChange={(e) => setFreq(e.target.value)}
      />
      <input
        value={duration}
        placeholder="Duration"
        aria-label="Duration"
        onChange={(e) => setDuration(e.target.value)}
      />
      <button onClick={submit} disabled={busy || !name.trim()} data-testid="add-med-save">
        Add
      </button>
      <button className="is-quiet" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </div>
  );
}

/** A field the doctor can fix, with the provenance line kept underneath it. */
function EditableField({
  label,
  value,
  spoken,
  locked,
  placeholder,
  multiline,
  onCommit,
}: {
  label: string;
  value: string;
  spoken: string;
  locked: boolean;
  placeholder: string;
  multiline?: boolean;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  if (locked) return <Provenance label={label} spoken={spoken} written={value || "—"} />;

  const commit = () => draft !== value && onCommit(draft);

  return (
    <div className="prov">
      <span className="prov-label">{label}</span>
      <div className="prov-body">
        {multiline ? (
          <textarea
            className="prov-in"
            value={draft}
            rows={2}
            aria-label={label}
            placeholder={placeholder}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
          />
        ) : (
          <input
            className="prov-in"
            value={draft}
            aria-label={label}
            placeholder={placeholder}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          />
        )}
        {spoken && <span className="prov-spoken">“{spoken}”</span>}
      </div>
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
