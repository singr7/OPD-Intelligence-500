"use client";

// The patient card (doc 03 §4/§5, doc 04 §3).
//
// Its single job: one patient's story, absorbed in twenty seconds. So the order
// on screen is the order of clinical urgency, not the order of the data model:
//
//   1. the red-flag strip — what could kill this patient today
//   2. the chief concern, then the symptoms table
//   3. everything else, collapsed (answers, timeline, trends, history)
//
// Red flags render as solid danger stamps carrying the rule's actual
// instruction, not pale badge chips: this is the one thing on the screen that
// must survive being glanced at, and a tinted pill next to a tinted pill is how
// an alarm becomes decoration.

import { useState } from "react";
import type { PatientCard as Card } from "../_lib/doctor";
import { Sparkline } from "./Sparkline";

const SEX_SHORT: Record<string, string> = { male: "M", female: "F", other: "—" };

export function PatientCard({
  card,
  busy,
  onAction,
}: {
  card: Card;
  busy: boolean;
  onAction: (action: "in_consult" | "done" | "no_show" | "lab_requeue") => void;
}) {
  const s = card.summary;
  const urgent = card.red_flags.filter((f) => f.severity === "urgent");
  const other = card.red_flags.filter((f) => f.severity !== "urgent");

  return (
    <article className="card" data-testid="patient-card">
      {/* 1. red flags, before anything else on the screen */}
      {card.red_flags.length > 0 && (
        <section className="flagstrip" data-testid="red-flag-strip">
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
        </section>
      )}

      {/* 2. who, and the concern */}
      <header className="who">
        <div className="who-l">
          <h1>{card.name}</h1>
          <p className="meta">
            {card.age != null && <span>{card.age}y</span>}
            {card.sex && <span>{SEX_SHORT[card.sex] ?? card.sex}</span>}
            {card.village && <span>{card.village}</span>}
            <span className="mrn">{card.mrn}</span>
          </p>
        </div>
        <div className="who-r">
          <span className="tok">{card.token_no ?? "—"}</span>
          <span className="tok-label">token</span>
        </div>
      </header>

      <p className="concern">{s.chief_concern ?? card.chief_complaint_en ?? card.chief_complaint}</p>

      {card.chief_complaint && (
        <p className="own-words" lang={card.intake_lang ?? "hi"}>
          &ldquo;{s.patient_words.quote ?? card.chief_complaint}&rdquo;
          {s.patient_words.english && <span className="gloss"> — {s.patient_words.english}</span>}
        </p>
      )}

      {s.symptoms.length > 0 && (
        <table className="symptoms">
          <thead>
            <tr>
              <th>Symptom</th>
              <th>Duration</th>
              <th>Severity</th>
            </tr>
          </thead>
          <tbody>
            {s.symptoms.map((row, i) => (
              <tr key={i}>
                <td>{row.symptom ?? "—"}</td>
                <td>{row.duration ?? "—"}</td>
                <td>{row.severity ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {s.unclear.length > 0 && (
        <p className="unclear">
          Unclear — please confirm: {s.unclear.join("; ")}
        </p>
      )}

      {/* the doctor's one-tap actions (S8 queue verbs) */}
      <div className="actions">
        <button
          className="act primary"
          disabled={busy}
          onClick={() => onAction("in_consult")}
          title="Mark this patient as being seen"
        >
          Start consult
        </button>
        <button className="act" disabled={busy} onClick={() => onAction("lab_requeue")}>
          Send to lab &amp; re-queue
        </button>
        <button className="act" disabled={busy} onClick={() => onAction("no_show")}>
          No-show
        </button>
        <button className="act" disabled={busy} onClick={() => onAction("done")}>
          Done
        </button>
      </div>

      {/* 3. everything else, collapsed */}
      {s.since_last_visit.length > 0 && (
        <Fold title="Since last visit" count={s.since_last_visit.length} open>
          <ul className="lines">
            {s.since_last_visit.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </Fold>
      )}

      {s.hpi.length > 0 && (
        <Fold title="History of presenting illness" count={s.hpi.length}>
          <ul className="lines">
            {s.hpi.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </Fold>
      )}

      {s.history_meds.length > 0 && (
        <Fold title="History & current medicines" count={s.history_meds.length}>
          <ul className="lines">
            {s.history_meds.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </Fold>
      )}

      {card.trends.length > 0 && (
        <Fold title="Check-in trend" count={card.trends.length}>
          <ul className="trends">
            {card.trends.map((t) => {
              const first = t.points[0].value;
              const last = t.points[t.points.length - 1].value;
              const rising = last > first;
              return (
                <li key={t.symptom}>
                  <span className="tname">{t.symptom}</span>
                  <Sparkline points={t.points} rising={rising} />
                  <span className={`tdelta ${rising ? "up" : "down"}`}>
                    {first} → {last}
                  </span>
                </li>
              );
            })}
          </ul>
        </Fold>
      )}

      {card.answers.length > 0 && (
        <Fold title="Intake answers" count={card.answers.length}>
          <ul className="answers">
            {card.answers.map((a) => (
              <li key={a.node_id} className={a.flagged ? "flagged" : undefined}>
                <span className="q">{a.question}</span>
                <span className="a">
                  {a.answer}
                  {a.said && a.said !== a.answer && <em className="said"> &ldquo;{a.said}&rdquo;</em>}
                </span>
              </li>
            ))}
          </ul>
        </Fold>
      )}

      {card.timeline.length > 1 && (
        <Fold title="Past visits" count={card.timeline.length - 1}>
          <ol className="timeline">
            {card.timeline.map((v) => (
              <li key={v.visit_id} className={v.is_current ? "now" : undefined}>
                <span className="tdate">{v.date}</span>
                <span className="tdept">{v.department_name}</span>
                <span className="tcc">{v.chief_complaint ?? "—"}</span>
                <span className="tstatus">{v.is_current ? "today" : v.status}</span>
              </li>
            ))}
          </ol>
        </Fold>
      )}
    </article>
  );
}

function Fold({
  title,
  count,
  open = false,
  children,
}: {
  title: string;
  count?: number;
  open?: boolean;
  children: React.ReactNode;
}) {
  const [isOpen, setOpen] = useState(open);
  return (
    <section className={`fold ${isOpen ? "open" : ""}`}>
      <button className="fold-h" onClick={() => setOpen((v) => !v)} aria-expanded={isOpen}>
        <span className="chev" aria-hidden="true">
          ›
        </span>
        <span>{title}</span>
        {count != null && <span className="fold-n">{count}</span>}
      </button>
      {isOpen && <div className="fold-b">{children}</div>}
    </section>
  );
}
