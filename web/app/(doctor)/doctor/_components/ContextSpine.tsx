"use client";

// The context spine (plan §4.2) — the answer to "stay in context".
//
// Sticky, and it **never unmounts for any state of the consult**: not while the
// doctor dictates, not while they read the note, not while they prescribe. That
// is the whole point. The screen this replaces surfaced a penicillin anaphylaxis
// as a banner at prescribing time, by which point the plan was already composed.
//
// It carries exactly four things and refuses more:
//
//   1. identity — name, age/sex, MRN, token, visit state, language
//   2. the working diagnosis, one line, never truncated
//   3. allergies
//   4. red flags — label, instruction and provenance, above all routine content
//
// Vitals, history and the summary live in the work area below. Anything that
// wants a fifth permanent slot is asking for the spine to stop being readable.
//
// **Session MRD2 added a fifth slot anyway, and it is worth saying why.** The
// clinical-intelligence plan (§1.5) asks for `Reports: 2 new · 4 values flagged`
// here, and the whole stated intent of that module is that the doctor knows what
// the patient's papers say *before* the patient is in the room — which a badge
// on a tab they have not opened does not achieve on its own.
//
// So it is one line, and it is held to the rules that keep the spine readable:
// it never wraps to two, it is a **link into the tab** rather than content in
// its own right (nothing here is a value or a number a doctor could act on
// without opening the reading), and it is amber at its loudest. It is a status,
// not an alarm — red on this console stays reserved for the deterministic
// red-flag lane above it. A sixth slot should still be refused.
//
// Two rules the strips follow, both from the plan:
//
// * **Only what is present is rendered as danger.** Ruled-out criteria — "no
//   bone pain", "morning stiffness threshold not met" — are reassuring absences,
//   and styling an absence in danger red inverts the colour's meaning. They
//   belong in the Overview's reasoning, in neutral treatment.
// * **Absence of the strip and absence of flags must not look identical.** With
//   no red flags the strip says so plainly in a calm state; it does not vanish,
//   because a missing strip is indistinguishable from a strip that failed to
//   load.

import type { ImagingLookup } from "@/app/_lib/imaging";
import { imagingSpineLine } from "@/app/_lib/imaging";
import type { MedicalDocument } from "@/app/_lib/records";
import { documentTally } from "@/app/_lib/records";
import type { PatientCard as Card } from "../_lib/doctor";
import { spineLine } from "../_lib/allergies";

const SEX_SHORT: Record<string, string> = { male: "M", female: "F", other: "—" };

const LANG_NAME: Record<string, string> = {
  hi: "Hindi",
  en: "English",
  mr: "Marathi",
  te: "Telugu",
};

const STATE_LABEL: Record<string, string> = {
  waiting: "Waiting",
  called: "Called",
  in_consult: "In room",
  lab_requeue: "Back from lab",
  done: "Completed",
  no_show: "No-show",
};

export function ContextSpine({
  card,
  isMine,
  documents,
  documentsLoading,
  imaging,
  onOpenReports,
  onOpenAllergies,
}: {
  card: Card;
  isMine: boolean;
  documents: MedicalDocument[];
  documentsLoading: boolean;
  /** M3. Folded into the Reports line rather than given a slot of its own —
   *  see the note above about refusing a sixth. */
  imaging: ImagingLookup;
  onOpenReports: () => void;
  onOpenAllergies: () => void;
}) {
  const urgent = card.red_flags.filter((f) => f.severity === "urgent");
  const other = card.red_flags.filter((f) => f.severity !== "urgent");
  const tally = documentTally(documents);
  const scans = imagingSpineLine(imaging);
  const allergy = spineLine(card.allergies);

  return (
    <section className="spine-ctx" data-testid="context-spine">
      {/* 1. identity */}
      <header className="cx-id">
        <div className="cx-who">
          <h1>{card.name}</h1>
          <p className="cx-meta">
            {card.age != null && <span>{card.age}y</span>}
            {card.sex && <span>{SEX_SHORT[card.sex] ?? card.sex}</span>}
            <span className="cx-mrn">{card.mrn}</span>
            <span>{LANG_NAME[card.lang] ?? card.lang}</span>
            {card.entry_state && (
              <span className={`cx-state state-${card.entry_state}`}>
                {STATE_LABEL[card.entry_state] ?? card.entry_state}
              </span>
            )}
            {/* Reading a colleague's patient is legitimate — cover, a lab
                re-queue, a second opinion — but it should never be a surprise
                discovered halfway through writing the note. */}
            {!isMine && card.assigned_doctor_name && (
              <span className="cx-owner" data-testid="spine-owner">
                {card.assigned_doctor_name}&rsquo;s patient
              </span>
            )}
            {!isMine && !card.assigned_doctor_name && (
              <span className="cx-owner pool" data-testid="spine-owner">
                No doctor assigned
              </span>
            )}
          </p>
        </div>
        {/* The token gets the train-board treatment: tabular numerals, large.
            It is what reconnects this console to the board and the coordinator
            when someone asks "who is 14?" across the corridor. */}
        <div className="cx-tok">
          <span className="cx-tok-n">{card.token_no ?? "—"}</span>
          <span className="cx-tok-l">token</span>
        </div>
      </header>

      {/* 2. diagnosis — one line, never truncated */}
      <p className="cx-dx" data-testid="spine-diagnosis">
        {card.diagnosis ? (
          <>
            <span className="cx-dx-t">{card.diagnosis.text}</span>
            {!card.diagnosis.is_current_visit && (
              <span className="cx-dx-src"> · from the note of {card.diagnosis.on}</span>
            )}
          </>
        ) : (
          <span className="cx-dx-none">No diagnosis recorded yet</span>
        )}
      </p>

      {/* 3. allergies (SESSION-ALLERGY). For six sessions this line said
          "not captured by this system yet", because nothing captured one and
          saying so was the only honest thing available. It now renders a real
          derivation — and keeps the refusal that made the old line right: it
          never says "no known allergies", only who said what, and when.

          A button rather than text, because the doctor can now do something
          about it. It is the one control in the spine that opens over the
          console instead of switching a tab: recording an allergy takes ten
          seconds and must not cost the doctor the tab they were reading. */}
      <button
        className={`cx-allergy tone-${allergy.tone}`}
        onClick={onOpenAllergies}
        data-testid="spine-allergies"
        data-state={card.allergies.state}
        data-tone={allergy.tone}
      >
        <span className="cx-allergy-l">Allergies</span>
        <span className="cx-allergy-t">{allergy.text}</span>
        {allergy.note && <span className="cx-allergy-p">{allergy.note}</span>}
      </button>

      {/* 4. red flags, above all routine content */}
      {card.red_flags.length > 0 ? (
        <div className="cx-flags" data-testid="red-flag-strip">
          {[...urgent, ...other].map((flag) => (
            <div key={flag.id} className={`stamp ${flag.severity}`}>
              <span className="stamp-mark" aria-hidden="true">
                {flag.severity === "urgent" ? "!" : "•"}
              </span>
              <span className="stamp-body">
                <strong>{flag.label}</strong>
                {/* The rule's instruction is patient-facing copy — the words the
                    kiosk actually spoke. Labelled, so the doctor reads it as
                    "what they were already told", not as an instruction to them. */}
                {flag.instruction && (
                  <em>
                    <span className="stamp-said">Patient was told:</span> {flag.instruction}
                  </em>
                )}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="cx-noflags" data-testid="no-red-flags">
          No red flags fired during intake
        </p>
      )}

      {/* 5. the papers (MRD2). Below the red flags on purpose: those stay
          nearest the top, and this is a status line, not a finding. */}
      <button
        className={`cx-reports ${tally.awaitingReview > 0 ? "attn" : ""}`}
        onClick={onOpenReports}
        data-testid="spine-reports"
      >
        <span className="cx-reports-l">Reports</span>
        {documentsLoading ? (
          // Never "none" while we do not know. A spine that says "no reports"
          // for the half-second before the fetch lands is a spine that told the
          // doctor a clinical fact it had not checked.
          <span className="cx-reports-t">checking…</span>
        ) : tally.onFile === 0 ? (
          <span className="cx-reports-t none">nothing scanned for this patient</span>
        ) : (
          <span className="cx-reports-t">
            {tally.onFile} on file
            {tally.flagged > 0 && <> · {tally.flagged} flagged</>}
            {tally.awaitingReview > 0 && <> · {tally.awaitingReview} unverified</>}
            {tally.failed > 0 && <> · {tally.failed} unread</>}
          </span>
        )}
        {/* M3 rides on this line rather than taking a sixth slot. It is the
            same kind of fact — what is on file about this patient from
            elsewhere — and the same kind of control: a link into the tab, with
            no number a doctor could act on without opening it. `null` when the
            PACS is switched off, because an installation without one should
            not carry a permanent "imaging: off" line on every patient. */}
        {scans && (
          <span
            className={`cx-scans ${imaging.state === "unreachable" ? "attn" : ""}`}
            data-testid="spine-imaging"
          >
            {scans}
          </span>
        )}
      </button>
    </section>
  );
}
