"use client";

// The Research tab (plan §4) — the fourth clinical-intelligence module.
//
// Its single job: let the doctor look something up about *this* patient without
// leaving the console, and see exactly what left the box before it left.
//
// Three things, in this order:
//
//   1. **The context strip — what will be sent, and the switch for each line.**
//      This is first because it is the module's whole claim. The plan asks for
//      "the doctor can see and trim exactly what leaves the box", and a control
//      that lives below the conversation is a control nobody reads until after
//      the first question has already gone.
//   2. **The conversation**, framed as reference throughout rather than at the
//      bottom in small print.
//   3. **The question box**, with what is left of today's turns beside it.
//
// ## Why a tab and not a dock
//
// M4 made the ambient note a *dock* and argued it: the plan says "capturing
// observations **while browsing**", and a tab would replace the thing being
// read, which is the failure the context spine exists to prevent.
//
// The opposite argument applies here, which is why this is a tab. Reading an
// evidence summary is not something a doctor does *while* reading something
// else — it is the thing they are doing, it wants the width, and a 52vh drawer
// would put a twelve-line answer in a four-line window. The spine still sits
// above it and still never unmounts, so identity, diagnosis, allergies and red
// flags are on screen the whole time.
//
// It also means there are not two docks fighting for the bottom of the screen.
// The note dock owns that space, and it should keep owning it.
//
// **No sixth spine slot.** MRD2 took a fifth for Reports and wrote down the
// argument for refusing a sixth; nothing here overrides it. A research thread
// has no count a doctor should act on without opening it, which is exactly the
// test that slot has to pass.
//
// ## The deliberate aesthetic risk (doc 04 §5, one per surface)
//
// **Unticked context is struck through, not removed.** The obvious build hides
// what you turn off. That would make a withheld line and a line this patient
// never had look identical — the same failure the spine's "No red flags fired"
// exists to avoid, and the same one the server answers with its `absent` list.
// So a line the doctor withholds stays exactly where it was, struck and dimmed,
// and "I decided not to send that" is legible at a glance instead of being
// something they have to remember. Everything else on this screen stays quiet.
//
// ## What this surface must never do
//
// Render the answer as anything but text. There is no markdown pass and no
// `dangerouslySetInnerHTML` here: the model writes prose, the panel shows prose,
// and a renderer that turned a model's asterisks into a styled clinical
// document would be dressing reference material as a record.

import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError } from "@/app/_lib/queue";
import {
  ask,
  fetchPanel,
  initialSelection,
  ResearchUnavailable,
  type ResearchPanel,
  type ResearchTurn,
} from "../_lib/research";

/** Provider down or budget spent. Both close the composer; they say different
 *  things, because "come back tomorrow" and "the vendor is unreachable" are
 *  different facts and a doctor plans around them differently. */
type Halt = { kind: "provider" | "budget"; message: string } | null;

export function ResearchTab({
  token,
  visitId,
  onAuthError,
}: {
  token: string;
  visitId: string;
  onAuthError: () => void;
}) {
  const [panel, setPanel] = useState<ResearchPanel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [halt, setHalt] = useState<Halt>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    fetchPanel(token, visitId)
      .then((next) => {
        if (!live) return;
        setPanel(next);
        setSelected(initialSelection(next));
        setError(null);
      })
      .catch((err) => {
        if (!live) return;
        if (err instanceof AuthError) return onAuthError();
        setError(err instanceof Error ? err.message : "could not open the assistant");
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [token, visitId, onAuthError]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }, []);

  const submit = useCallback(
    async (text: string) => {
      const asked = text.trim();
      if (!asked || asking || !panel) return;
      setAsking(true);
      setError(null);
      try {
        const result = await ask(token, visitId, asked, selected);
        setPanel((prev) =>
          prev ? { ...prev, turns: [...prev.turns, result.turn], budget: result.budget } : prev,
        );
        setQuestion("");
        if (result.budget.remaining === 0) {
          setHalt({
            kind: "budget",
            message: `That is ${result.budget.limit} research questions today. The assistant resumes tomorrow.`,
          });
        }
      } catch (err) {
        if (err instanceof AuthError) return onAuthError();
        if (err instanceof ResearchUnavailable) {
          // "Provider down → the panel says so and closes; nothing queues"
          // (plan §4.1). The question stays in the box, because the doctor
          // still has it and retyping it is the one cost we can spare them.
          setHalt({ kind: err.kind, message: err.message });
        } else {
          setError(err instanceof Error ? err.message : "the assistant did not answer");
        }
      } finally {
        setAsking(false);
      }
    },
    [asking, panel, token, visitId, selected, onAuthError],
  );

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [panel?.turns.length, asking]);

  if (loading) return <p className="work-empty">Opening the assistant…</p>;

  if (error && !panel) {
    return (
      <p className="work-empty err" data-testid="research-error">
        {error} This is the console failing to ask, not the assistant refusing.
      </p>
    );
  }

  if (!panel) return null;

  if (!panel.enabled) {
    return (
      <p className="work-empty" data-testid="research-off">
        The research assistant is switched off on this installation. Nothing is queued and nothing
        was sent.
      </p>
    );
  }

  const composerClosed = halt !== null;

  return (
    <section className="rsx" data-testid="research-tab">
      {/* 1. What will be sent. First on the screen, deliberately. */}
      <ContextStrip panel={panel} selected={selected} onToggle={toggle} disabled={asking} />

      {/* 2. The framing, above the conversation rather than under it in small
             print. It is the most important sentence on this screen and it is
             not a footnote. */}
      <p className="rsx-frame" data-testid="research-disclaimer">
        <strong>Reference only.</strong> This is not a recommendation, it has not seen your
        patient, and nothing here reaches their record. Verify against your local protocol before
        acting on any of it.
      </p>

      {/* 3. The conversation. */}
      <div className="rsx-thread">
        {panel.turns.length === 0 && !asking && (
          <Suggestions
            suggestions={panel.suggestions}
            disabled={composerClosed}
            onPick={(text) => submit(text)}
          />
        )}
        {panel.turns.map((turn) => (
          <Exchange key={turn.id} turn={turn} />
        ))}
        {asking && (
          <p className="rsx-thinking" data-testid="research-thinking">
            Looking it up…
          </p>
        )}
        <div ref={endRef} />
      </div>

      {error && (
        <p className="rsx-err" data-testid="research-turn-error">
          {error}
        </p>
      )}

      {/* The halt states. Both close the composer; neither queues anything. */}
      {halt && (
        <p className={`rsx-halt ${halt.kind}`} data-testid="research-halt">
          {halt.kind === "provider" ? (
            <>
              <strong>The assistant is unreachable.</strong> {halt.message}. Nothing was sent and
              nothing is waiting to be — ask again when it is back.
            </>
          ) : (
            <>
              <strong>No research turns left today.</strong> {halt.message}
            </>
          )}
        </p>
      )}

      {!composerClosed && (
        <Composer
          question={question}
          onQuestion={setQuestion}
          onSubmit={() => submit(question)}
          busy={asking}
          budget={panel.budget}
        />
      )}
    </section>
  );
}

// -- the context strip --------------------------------------------------------

function ContextStrip({
  panel,
  selected,
  onToggle,
  disabled,
}: {
  panel: ResearchPanel;
  selected: string[];
  onToggle: (id: string) => void;
  disabled: boolean;
}) {
  const kept = panel.context.filter((item) => selected.includes(item.id)).length;

  return (
    <section className="rsx-ctx" data-testid="research-context">
      <header className="rsx-ctx-h">
        <h3>What will be sent</h3>
        <p className="rsx-ctx-n" data-testid="research-context-count">
          {panel.context.length === 0
            ? "nothing on file to send"
            : `${kept} of ${panel.context.length} lines`}
        </p>
      </header>

      {/* No name, no phone, no MRN and no id is on this list, and the line says
          so plainly rather than leaving the doctor to notice the absence. It is
          the claim the whole module rests on, so it is stated where the claim
          is being made. */}
      <p className="rsx-ctx-phi">
        Age goes as a band, never a date of birth. Name, phone, MRN and UHC ID are never sent.
      </p>

      <ul className="rsx-items">
        {panel.context.map((item) => {
          const on = selected.includes(item.id);
          return (
            <li key={item.id} className={on ? "" : "off"}>
              <label>
                <input
                  type="checkbox"
                  checked={on}
                  disabled={disabled}
                  onChange={() => onToggle(item.id)}
                  data-testid={`research-ctx-${item.id}`}
                />
                {/* Verbatim. A "view what we send" control that paraphrases
                    what it sends is worse than not having one. */}
                <span className="rsx-item-t">{item.text}</span>
              </label>
              <p className="rsx-item-src">
                {item.source}
                {item.caveat && (
                  <span className="rsx-item-caveat" data-testid={`research-caveat-${item.id}`}>
                    {" "}
                    {item.caveat}
                  </span>
                )}
              </p>
            </li>
          );
        })}
      </ul>

      {/* A source that is empty and a source the console forgot to build must
          not look the same — the spine's rule, applied here. */}
      {panel.absent.length > 0 && (
        <ul className="rsx-absent" data-testid="research-absent">
          {panel.absent.map(([label, why]) => (
            <li key={label}>
              <span className="rsx-absent-l">{label}</span> — {why}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// -- the conversation ---------------------------------------------------------

function Suggestions({
  suggestions,
  disabled,
  onPick,
}: {
  suggestions: string[];
  disabled: boolean;
  onPick: (text: string) => void;
}) {
  if (suggestions.length === 0) {
    return (
      <p className="rsx-empty" data-testid="research-empty">
        Ask anything about this presentation. The conversation is kept with this visit.
      </p>
    );
  }
  return (
    <div className="rsx-suggest" data-testid="research-suggestions">
      <p className="rsx-suggest-l">Starting points</p>
      {suggestions.map((text) => (
        <button key={text} disabled={disabled} onClick={() => onPick(text)}>
          {text}
        </button>
      ))}
    </div>
  );
}

function Exchange({ turn }: { turn: ResearchTurn }) {
  const [showSent, setShowSent] = useState(false);

  return (
    <article className="rsx-turn" data-testid="research-turn">
      <p className="rsx-q">{turn.question}</p>

      <div className="rsx-a">
        {/* Prose in, prose out. No markdown pass, no HTML — see the header. */}
        {paragraphs(turn.answer).map((para, i) => (
          <p key={i}>{para}</p>
        ))}
      </div>

      <footer className="rsx-turn-f">
        {turn.model && <span className="rsx-model">{turn.model}</span>}
        <button
          className="rsx-sent-toggle"
          aria-expanded={showSent}
          onClick={() => setShowSent((v) => !v)}
          data-testid="research-sent-toggle"
        >
          {turn.context_sent.length === 0
            ? "no patient context was sent"
            : `${turn.context_sent.length} line${turn.context_sent.length === 1 ? "" : "s"} of context sent`}
        </button>
      </footer>

      {/* What actually left the box with *this* question, frozen. Not
          re-derived from the current context: a lab value may have been
          re-flagged since, and the answer above was written against the old
          one. */}
      {showSent && turn.context_sent.length > 0 && (
        <ul className="rsx-sent" data-testid="research-sent">
          {turn.context_sent.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}
    </article>
  );
}

/** Split prose on blank lines. Deliberately the whole of the "rendering". */
function paragraphs(text: string): string[] {
  return text
    .split(/\n\s*\n|\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

// -- the question box ---------------------------------------------------------

function Composer({
  question,
  onQuestion,
  onSubmit,
  busy,
  budget,
}: {
  question: string;
  onQuestion: (text: string) => void;
  onSubmit: () => void;
  busy: boolean;
  budget: { used: number; limit: number; remaining: number };
}) {
  return (
    <div className="rsx-composer">
      <textarea
        value={question}
        onChange={(e) => onQuestion(e.target.value)}
        placeholder="Ask about this presentation…"
        rows={2}
        disabled={busy}
        data-testid="research-question"
        onKeyDown={(e) => {
          // Enter sends, Shift+Enter breaks the line. A question typed between
          // two patients should not need a mouse.
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSubmit();
          }
        }}
      />
      <div className="rsx-composer-r">
        {/* Amber only when it is nearly gone. A counter that is loud all day is
            a counter a doctor stops reading. */}
        <span
          className={`rsx-budget ${budget.remaining <= 5 ? "low" : ""}`}
          data-testid="research-budget"
        >
          {budget.remaining} of {budget.limit} left today
        </span>
        <button
          className="rsx-ask"
          onClick={onSubmit}
          disabled={busy || !question.trim()}
          data-testid="research-ask"
        >
          {busy ? "Asking…" : "Ask"}
        </button>
      </div>
    </div>
  );
}
