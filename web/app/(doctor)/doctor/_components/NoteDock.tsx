"use client";

// The ambient consult note (plan §3; doc 04 §3 doctor console, §5 anti-generic).
//
// **Its single job:** let a doctor say a thought out loud without leaving
// whatever they were reading, and confirm in one pass that what came back is
// what they meant.
//
// The three elements, in order:
//   1. the mic, and whether it is actually hearing the room;
//   2. what they said, beside what the machine made of it;
//   3. the line saying this is not a prescription.
//
// **Why it is a dock and not a tab.** The plan calls it "capturing observations
// *while browsing*", and that word decides the shape. A tab would replace the
// thing the doctor was looking at, which is the exact failure the context spine
// was built to fix in Session B. So this is a button that floats over the stage
// and a drawer that takes the lower half of it: the spine — identity, diagnosis,
// allergies, red flags — stays on screen through the whole capture, and so does
// the top of whatever tab they were reading.
//
// **The deliberate aesthetic risk for this surface (doc 04 §5) is the level
// ring.** The mic button wears a ring drawn from real RMS readings off the
// analyser, so a doctor mid-sentence can see the room is being heard without
// opening anything. It is held to Session C's rule, which is the only reason it
// is allowed to exist: **no ring without an analyser.** A ring that pulsed on a
// timer would be a claim that audio is being captured, made by an animation that
// cannot know. With no analyser there is a plain recording dot and an elapsed
// timer, which are both simply true.
//
// **What this surface must never grow.** There is no medication row here, no
// dose field, no print. A drug the doctor dictates lands in `plan_narrative` as
// their own prose and stays prose — the note says so in one quiet line, and the
// backend cannot do otherwise (`app/notes.py`). Prescriptions are the Consult
// tab, behind a formulary check and a signature.

import { Loader2, Mic, Square, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError } from "@/app/_lib/queue";
import type { ClinicalNote, NoteFields } from "../_lib/notes";
import {
  composeNote,
  confirmNote,
  correctNote,
  draftCount,
  EMPTY_FIELDS,
  fetchNotes,
  mapNote,
  startNote,
} from "../_lib/notes";
import { clock, useVoiceCapture } from "../_lib/useVoiceCapture";

const FIELD_LABEL: { key: keyof Omit<NoteFields, "tags">; label: string; hint: string }[] = [
  { key: "subjective", label: "Subjective", hint: "What the patient reports" },
  { key: "objective", label: "Objective", hint: "What you observed" },
  { key: "assessment", label: "Assessment", hint: "Where things stand" },
  { key: "plan_narrative", label: "Plan", hint: "What happens next — prose, not orders" },
];

export function NoteDock({
  token,
  visitId,
  patientName,
}: {
  token: string;
  visitId: string;
  patientName: string;
}) {
  const [notes, setNotes] = useState<ClinicalNote[]>([]);
  const [open, setOpen] = useState<ClinicalNote | null>(null);
  const [transcript, setTranscript] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Which visit `notes` belongs to. The dock is mounted across patient
   *  switches, and one patient's observations must never appear under another's
   *  name — the same guard the console puts on the reports fetch. */
  const forVisit = useRef<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const rows = await fetchNotes(token, visitId);
      if (forVisit.current !== visitId) return;
      setNotes(rows);
    } catch (err) {
      if (!(err instanceof AuthError)) setError("Could not load this visit's notes.");
    }
  }, [token, visitId]);

  useEffect(() => {
    forVisit.current = visitId;
    setNotes([]);
    setOpen(null);
    setTranscript("");
    setError(null);
    void reload();
  }, [visitId, reload]);

  const capture = useVoiceCapture({
    token,
    endpoint: "/notes/stt",
    currentTranscript: transcript,
    onTranscript: setTranscript,
    onError: setError,
  });

  /**
   * Stop, then file it: one note row, then the mapping.
   *
   * Split into two calls because the split is what protects the words. `start`
   * stores the transcript; if `map` then fails, the observation is already on
   * the record and the drawer opens on empty fields the doctor can type into.
   * A single save-everything call would lose the recording with the mapping.
   */
  const fileIt = useCallback(
    async (text: string) => {
      setBusy("saving");
      setError(null);
      try {
        const created = await startNote(token, visitId, text);
        setOpen(created);
        setBusy("mapping");
        try {
          const mapped = await mapNote(token, created.id);
          setOpen(mapped);
        } catch (err) {
          // The degraded state, and it is a state rather than an error: open the
          // fields so the doctor can write what they meant and still confirm.
          setError(
            err instanceof Error
              ? `${err.message} — your words are saved; write the note yourself.`
              : "The model could not structure this. Your words are saved.",
          );
          setOpen(await composeNote(token, created.id));
        }
        await reload();
      } catch (err) {
        if (err instanceof AuthError) return;
        setError(err instanceof Error ? err.message : "Could not save that note.");
      } finally {
        setBusy(null);
      }
    },
    [token, visitId, reload],
  );

  const onMic = useCallback(async () => {
    if (capture.recording) {
      capture.stop();
      return;
    }
    setError(null);
    setTranscript("");
    await capture.start();
  }, [capture]);

  // When the recording stops and the words have landed, file them. Keyed on the
  // transcript arriving rather than on the stop itself: Web Speech and the
  // server pass resolve at different moments, and filing on `stop` would post an
  // empty note on the browser where the server pass is the only path.
  const filed = useRef("");
  useEffect(() => {
    if (capture.recording || capture.transcribing || busy || open) return;
    const text = transcript.trim();
    if (!text || filed.current === text) return;
    filed.current = text;
    void fileIt(text);
  }, [capture.recording, capture.transcribing, transcript, busy, open, fileIt]);

  /**
   * Open a draft for review, opening its fields first if it has none.
   *
   * A note can reach this screen with `fields` still null — the tab was closed
   * between `start` and `map`, or the browser went away mid-capture. Rendering
   * the textareas anyway would offer the doctor an edit that `PATCH` refuses
   * ("this note has not been mapped yet"), so they would type a paragraph and
   * watch it fail to save. `compose` is the same verb the failed-mapping path
   * already calls; calling it here makes the two arrivals at this screen
   * identical.
   */
  const openNote = useCallback(
    async (note: ClinicalNote | null) => {
      if (!note) return;
      if (note.fields) {
        setOpen(note);
        return;
      }
      setBusy("opening");
      try {
        setOpen(await composeNote(token, note.id));
      } catch (err) {
        if (!(err instanceof AuthError)) {
          setError(err instanceof Error ? err.message : "Could not open that note.");
        }
      } finally {
        setBusy(null);
      }
    },
    [token],
  );

  const patch = useCallback(
    async (next: Partial<NoteFields>) => {
      if (!open) return;
      setBusy("saving");
      try {
        const updated = await correctNote(token, open.id, next);
        setOpen(updated);
      } catch (err) {
        setError(err instanceof Error ? err.message : "That edit did not save.");
      } finally {
        setBusy(null);
      }
    },
    [token, open],
  );

  const onConfirm = useCallback(async () => {
    if (!open) return;
    setBusy("confirming");
    try {
      await confirmNote(token, open.id);
      setOpen(null);
      setTranscript("");
      filed.current = "";
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That was refused.");
    } finally {
      setBusy(null);
    }
  }, [token, open, reload]);

  /**
   * Keep the spine on screen while the drawer is up.
   *
   * The first screenshot of this surface showed the drawer sitting over the
   * diagnosis, the allergies and the red flags — which is precisely the failure
   * this module is shaped to avoid, and the reason it is a drawer and not a tab.
   * Being in the DOM is not the claim; being *readable* is.
   *
   * Two halves: the body flag gives the console bottom padding so there is room
   * to scroll (see `NOTE_CSS`), and this scrolls the spine up to its sticky
   * offset so it pins under the app bar rather than sitting behind the drawer.
   * Computed against `.appbar` rather than a hard-coded 64, because a wrapped
   * app bar on a narrow window is taller and the spine's `top` follows it.
   */
  useEffect(() => {
    if (!open) {
      delete document.body.dataset.noteOpen;
      return;
    }
    document.body.dataset.noteOpen = "1";
    // Next frame, not this one. The flag above is what gives the console its
    // bottom padding, and until the browser has laid that out the page is still
    // its old height — so a `scrollTo` here is clamped to the old maximum and
    // silently does nothing. That is exactly how the first version of this
    // failed, with the spine left sitting behind the drawer.
    const frame = requestAnimationFrame(() => {
      const spine = document.querySelector<HTMLElement>('[data-testid="context-spine"]');
      const bar = document.querySelector<HTMLElement>(".appbar");
      if (!spine || !bar) return;
      const target = window.scrollY + spine.getBoundingClientRect().top - bar.offsetHeight;
      if (target > window.scrollY) window.scrollTo({ top: target });
    });
    return () => {
      cancelAnimationFrame(frame);
      delete document.body.dataset.noteOpen;
    };
  }, [open]);

  const drafts = draftCount(notes);
  const confirmed = notes.length - drafts;

  return (
    <>
      {/* -- 1. the mic ---------------------------------------------------- */}
      <div className="nd-fab-wrap">
        {capture.recording && (
          <div className="nd-live" data-testid="note-live">
            <time className="nd-live-t">{clock(capture.elapsed)}</time>
            <p className="nd-live-x">{transcript || "Listening…"}</p>
          </div>
        )}

        <button
          className={`nd-fab${capture.recording ? " is-rec" : ""}`}
          onClick={onMic}
          disabled={!!busy}
          data-testid="note-mic"
          aria-label={capture.recording ? "Stop and save this observation" : "Record an observation"}
          title={capture.recording ? "Stop and save" : "Record an observation"}
        >
          {/* The ring is real readings or it is not drawn. See the header. */}
          {capture.recording && capture.levels.length > 0 && (
            <LevelRing level={capture.levels[capture.levels.length - 1] ?? 0} />
          )}
          {busy || capture.transcribing ? (
            <Loader2 size={22} className="nd-spin" aria-hidden="true" />
          ) : capture.recording ? (
            <Square size={18} aria-hidden="true" />
          ) : (
            <Mic size={22} aria-hidden="true" />
          )}
          {!capture.recording && drafts > 0 && (
            <span className="nd-fab-n" data-testid="note-drafts">
              {drafts}
            </span>
          )}
        </button>

        {!capture.recording && !open && (
          <p className="nd-fab-l">
            {/* Always names what it records, before it says how many there are.
                The Consult tab puts a second microphone on screen — its own
                `Dictate`, which is the prescription path — and the first
                screenshot of this surface had the two distinguishable only by
                position, with this one labelled "20 notes to review". Which mic
                a doctor is about to speak a drug into should never be a thing
                they work out from a count. */}
            <span className="nd-fab-what">Observation</span>
            {drafts > 0 ? (
              <button
                className="nd-resume"
                data-testid="note-resume"
                onClick={() => void openNote(lastDraft(notes))}
              >
                {drafts} to review
              </button>
            ) : confirmed > 0 ? (
              <span data-testid="note-count">
                {confirmed} note{confirmed > 1 ? "s" : ""} this visit
              </span>
            ) : null}
          </p>
        )}
      </div>

      {/* -- 2 & 3. the review -------------------------------------------- */}
      {open && (
        <NoteReview
          note={open}
          patientName={patientName}
          busy={busy}
          error={error}
          onPatch={patch}
          onConfirm={onConfirm}
          onClose={() => {
            setOpen(null);
            setTranscript("");
            filed.current = "";
          }}
        />
      )}

      {!open && error && (
        <p className="nd-err" data-testid="note-error">
          {error}
        </p>
      )}
    </>
  );
}

/**
 * The level ring — one real RMS reading, drawn as a stroke.
 *
 * Never rendered without an analyser (the caller checks), so its presence is
 * itself the honest signal: a ring means readings are arriving.
 */
function LevelRing({ level }: { level: number }) {
  const r = 27;
  const circumference = 2 * Math.PI * r;
  // A floor of 8% so a quiet room still shows a live ring rather than reading as
  // a dead microphone — the doctor is checking that it hears *something*.
  const shown = 0.08 + Math.min(1, level) * 0.92;
  return (
    <svg className="nd-ring" viewBox="0 0 64 64" aria-hidden="true">
      <circle className="nd-ring-bg" cx="32" cy="32" r={r} />
      <circle
        className="nd-ring-fg"
        cx="32"
        cy="32"
        r={r}
        strokeDasharray={`${circumference * shown} ${circumference}`}
      />
    </svg>
  );
}

function lastDraft(notes: ClinicalNote[]): ClinicalNote | null {
  const drafts = notes.filter((n) => n.status === "draft");
  return drafts.length ? drafts[drafts.length - 1] : null;
}

// -- the review drawer --------------------------------------------------------

function NoteReview({
  note,
  patientName,
  busy,
  error,
  onPatch,
  onConfirm,
  onClose,
}: {
  note: ClinicalNote;
  patientName: string;
  busy: string | null;
  error: string | null;
  onPatch: (next: Partial<NoteFields>) => void;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const fields = note.fields ?? EMPTY_FIELDS;
  const mapped = note.mapped;
  const failed = !!note.mapping_error && !mapped;
  const empty =
    !fields.subjective.trim() &&
    !fields.objective.trim() &&
    !fields.assessment.trim() &&
    !fields.plan_narrative.trim() &&
    !fields.tags.problems.length &&
    !fields.tags.symptoms.length &&
    !fields.tags.followups.length;

  return (
    <section className="nd-drawer" data-testid="note-drawer" aria-label="Consult note">
      <header className="nd-head">
        <div className="nd-head-l">
          <h2>Observation</h2>
          <span className="nd-who">{patientName}</span>
          {/* Three states, because there are three, and the middle one used to
              lie. The badge said "AI-drafted" on a note that reached this screen
              with no mapping at all — the doctor had typed every word of it and
              the screen credited a model. `mapped` is the only thing that makes
              the AI claim true, so it is what the claim is keyed on. */}
          {failed ? (
            <span className="nd-badge warn" data-testid="note-degraded">
              not structured — your words are saved
            </span>
          ) : mapped ? (
            <span className="nd-badge" data-testid="note-badge">
              AI-drafted, unconfirmed
            </span>
          ) : (
            <span className="nd-badge plain" data-testid="note-badge">
              yours, unconfirmed
            </span>
          )}
        </div>
        <button className="nd-x" onClick={onClose} aria-label="Close, keep as a draft">
          <X size={17} aria-hidden="true" />
        </button>
      </header>

      {error && <p className="nd-drawer-err">{error}</p>}

      <div className="nd-body">
        {/* what they said */}
        <div className="nd-said">
          <h3>You said</h3>
          {note.transcript ? (
            <blockquote data-testid="note-transcript">{note.transcript}</blockquote>
          ) : (
            <p className="nd-said-none">
              Nothing was recorded — this note is being typed. That is a complete way to make one.
            </p>
          )}
          {note.model && (
            <p className="nd-prov">
              Read by {note.model}
              {note.prompt_ref ? ` · ${note.prompt_ref}` : ""}
            </p>
          )}

          {/* The tags live under the transcript rather than under the four
              fields, and the first screenshot is why. On the right they sat
              below the fold — the one genuinely new thing on this screen,
              needing a scroll to find — while this column held two lines of
              speech and a column of empty space. They belong beside the words
              they were drawn from anyway. */}
          <TagStrip tags={fields.tags} onPatch={(tags) => onPatch({ tags })} />
        </div>

        {/* what the machine made of it */}
        <div className="nd-fields">
          {FIELD_LABEL.map(({ key, label, hint }) => (
            <Field
              key={key}
              label={label}
              hint={hint}
              value={fields[key]}
              changed={!!mapped && mapped[key] !== fields[key]}
              onCommit={(next) => next !== fields[key] && onPatch({ [key]: next })}
            />
          ))}
        </div>
      </div>

      <footer className="nd-foot">
        {/* 3. the line. Quiet, permanent, and load-bearing. */}
        <p className="nd-rule">
          A note records what you observed. It never prescribes — write medicines on the{" "}
          <strong>Consult</strong> tab, where the formulary check and your signature are.
        </p>
        <div className="nd-foot-act">
          <button className="nd-later" onClick={onClose} disabled={!!busy}>
            Keep as draft
          </button>
          <button
            className="nd-confirm"
            onClick={onConfirm}
            disabled={!!busy || empty}
            data-testid="note-confirm"
            title={empty ? "There is nothing in this note yet" : undefined}
          >
            {busy === "confirming" ? "Confirming…" : "Confirm note"}
          </button>
        </div>
      </footer>
    </section>
  );
}

/** One prose field. `changed` marks where the doctor differs from the model —
 *  the review is a diff, not "here is some text, trust it". */
function Field({
  label,
  hint,
  value,
  changed,
  onCommit,
}: {
  label: string;
  hint: string;
  value: string;
  changed: boolean;
  onCommit: (next: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  return (
    <label className={`nd-f${changed ? " is-edited" : ""}`}>
      <span className="nd-f-l">
        {label}
        {changed && <em data-testid={`edited-${label.toLowerCase()}`}>edited</em>}
      </span>
      <textarea
        value={draft}
        rows={2}
        placeholder={hint}
        data-testid={`note-${label.toLowerCase()}`}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => onCommit(draft.trim())}
      />
    </label>
  );
}

/**
 * The tags, as removable chips.
 *
 * Removable because they are the countable part and a doctor is the only one
 * entitled to decide this consult was about that problem — `analytics.note_tags`
 * counts confirmed notes precisely because this screen exists. A symptom shows
 * its grade only when the doctor said one; a chip with no grade is silent about
 * severity rather than implying none.
 */
function TagStrip({ tags, onPatch }: { tags: NoteFields["tags"]; onPatch: (t: NoteFields["tags"]) => void }) {
  const nothing = !tags.problems.length && !tags.symptoms.length && !tags.followups.length;
  if (nothing) {
    return (
      <p className="nd-tags-none" data-testid="note-tags-none">
        No tags — this note counts as an observation and contributes nothing to the clinic totals.
      </p>
    );
  }

  return (
    <div className="nd-tags" data-testid="note-tags">
      {tags.problems.length > 0 && (
        <Row label="Problems">
          {tags.problems.map((p) => (
            <Chip
              key={p}
              text={p}
              onRemove={() => onPatch({ ...tags, problems: tags.problems.filter((x) => x !== p) })}
            />
          ))}
        </Row>
      )}
      {tags.symptoms.length > 0 && (
        <Row label="Symptoms">
          {tags.symptoms.map((s) => (
            <Chip
              key={s.name}
              text={s.name}
              // Shown only when spoken. The absence of a grade is not "grade 0".
              grade={s.grade_mentioned}
              onRemove={() =>
                onPatch({ ...tags, symptoms: tags.symptoms.filter((x) => x.name !== s.name) })
              }
            />
          ))}
        </Row>
      )}
      {tags.followups.length > 0 && (
        <Row label="Before next visit">
          {tags.followups.map((fu) => (
            <Chip
              key={fu}
              text={fu}
              onRemove={() => onPatch({ ...tags, followups: tags.followups.filter((x) => x !== fu) })}
            />
          ))}
        </Row>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="nd-tagrow">
      <span className="nd-tagrow-l">{label}</span>
      <div className="nd-tagrow-c">{children}</div>
    </div>
  );
}

function Chip({
  text,
  grade,
  onRemove,
}: {
  text: string;
  grade?: string | null;
  onRemove: () => void;
}) {
  return (
    <span className="nd-chip">
      {text}
      {grade && (
        <b className="nd-chip-g" title="The grade you said">
          G{grade}
        </b>
      )}
      <button onClick={onRemove} aria-label={`Remove ${text}`}>
        <X size={12} aria-hidden="true" />
      </button>
    </span>
  );
}
