"use client";

// The work area under the context spine: Overview, Intake answers, History.
// (The fourth tab, Consult, is the dictation panel and is mounted by `Console`.)
//
// What moved out of here in Session B is as important as what stayed. Identity,
// the token, the diagnosis, allergies and the red-flag strip all belong to the
// spine now, because they must survive dictating and prescribing — this file
// used to render the flags at the top of a card that unmounted the moment the
// doctor started writing.
//
// What is left is the 20-second read, and it follows two rules from the plan:
//
// * **Unframed sections, no nested cards** (doc 14 principle 5). Headings and
//   whitespace do the grouping. The screen this replaces nested cards three
//   deep, which is how a dense screen becomes an unreadable one.
// * **No confidence percentage.** The provenance line states four things a
//   doctor can actually weigh — who answered, on which tier, in which language,
//   and when it finished — instead of one number nobody can calibrate.

import type { PatientCard as Card } from "../_lib/doctor";
import { Sparkline } from "./Sparkline";
import type { WorkTab } from "./WorkTabs";

const TIER_LABEL: Record<string, string> = {
  prerecorded: "tap-only, pre-recorded prompts",
  conversational: "conversational voice",
  adaptive: "adaptive follow-ups",
};

const LANG_NAME: Record<string, string> = {
  hi: "Hindi",
  en: "English",
  mr: "Marathi",
  te: "Telugu",
};

export function PatientCard({ card, tab }: { card: Card; tab: WorkTab }) {
  return (
    <article className="work" data-testid="patient-card">
      {tab === "overview" && <Overview card={card} />}
      {tab === "answers" && <Answers card={card} />}
      {tab === "history" && <History card={card} />}
    </article>
  );
}

function Overview({ card }: { card: Card }) {
  const s = card.summary;
  return (
    <>
      <p className="concern">
        {s.chief_concern ?? card.chief_complaint_en ?? card.chief_complaint ?? "—"}
      </p>

      {card.chief_complaint && (
        <p className="own-words" lang={card.intake_lang ?? "hi"}>
          &ldquo;{s.patient_words.quote ?? card.chief_complaint}&rdquo;
          {s.patient_words.english && <span className="gloss"> — {s.patient_words.english}</span>}
        </p>
      )}

      {s.symptoms.length > 0 && (
        <Section title="Symptoms">
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
        </Section>
      )}

      {s.since_last_visit.length > 0 && (
        <Section title="Since last visit">
          <ul className="lines">
            {s.since_last_visit.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </Section>
      )}

      {s.hpi.length > 0 && (
        <Section title="History of presenting illness">
          <ul className="lines">
            {s.hpi.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </Section>
      )}

      {s.unclear.length > 0 && (
        <p className="unclear">Unclear — please confirm: {s.unclear.join("; ")}</p>
      )}

      {/* The provenance line. Four facts, stated plainly, in place of the
          confidence percentage this screen used to carry. */}
      <p className="provenance" data-testid="provenance">
        Answered by <strong>{card.caregiver_answered ? "a caregiver" : "the patient"}</strong>
        {card.intake_lang && <> in {LANG_NAME[card.intake_lang] ?? card.intake_lang}</>}
        {card.tier && <> · {TIER_LABEL[card.tier] ?? card.tier}</>}
        {card.completed_at && <> · finished {new Date(card.completed_at).toLocaleTimeString()}</>}
      </p>
    </>
  );
}

function Answers({ card }: { card: Card }) {
  if (card.answers.length === 0) {
    return <p className="work-empty">This intake recorded no answers.</p>;
  }
  return (
    <Section title="The questions as they were asked">
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
    </Section>
  );
}

function History({ card }: { card: Card }) {
  const s = card.summary;
  const past = card.timeline.filter((v) => !v.is_current);
  return (
    <>
      {/* Allergies appear here in full *and* in the spine (plan §4.3). Here the
          full version is an honest statement of a gap rather than a list. */}
      <Section title="Allergies">
        <p className="lines-note" data-testid="history-allergies">
          Nothing in this system captures allergies yet — not the kiosk intake, not the consult
          note. Treat this as unknown and ask, rather than as an empty list.
        </p>
      </Section>

      {s.history_meds.length > 0 && (
        <Section title="Conditions & current medicines">
          <ul className="lines">
            {s.history_meds.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Past visits" count={past.length}>
        {past.length === 0 ? (
          <p className="lines-note">This is their first recorded visit.</p>
        ) : (
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
        )}
      </Section>

      {card.trends.length > 0 && (
        <Section title="Check-in trend">
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
        </Section>
      )}
    </>
  );
}

/** An unframed section: a heading, a rule, and the content. No card, no nesting
 *  (doc 14 principle 5). */
function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section className="wsec">
      <h2>
        {title}
        {count != null && count > 0 && <span className="wsec-n">{count}</span>}
      </h2>
      {children}
    </section>
  );
}
