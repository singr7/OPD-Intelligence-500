// Typed client for the research assistant (backend app/routes/research.py).
//
// **There is no field in this file that carries context text to the server, and
// adding one would defeat the module.** `ask` sends `include: string[]` — the
// ids of the assembled items the doctor kept — and the server re-derives the
// words from the database. That asymmetry is what makes "the doctor can trim
// what we send" different from "the browser can send anything it likes to a
// vendor", and only the first is compatible with the plan's PHI posture.
//
// There is also no `sign`, no `apply` and no `accept`. A research answer cannot
// be adopted into the record; what a doctor takes from one they write
// themselves, on the Consult tab, in their own words.

import { API_BASE, AuthError } from "@/app/_lib/queue";

/** One line the doctor can see, untick, and hold us to. `text` is verbatim what
 *  goes to the vendor — the panel renders it unchanged, because a "view what we
 *  send" control that paraphrases what it sends is worse than none. */
export type ContextItem = {
  id: string;
  label: string;
  text: string;
  source: string;
  /** A reason to look twice before sending — an unverified machine reading, say. */
  caveat: string;
};

export type ResearchTurn = {
  id: string;
  question: string;
  answer: string;
  /** The lines that actually left the box with this question, frozen at the
   *  time. Not re-derived: a lab value may have been re-flagged since. */
  context_sent: string[];
  model: string | null;
  created_at: string;
};

export type Budget = { used: number; limit: number; remaining: number };

export type ResearchPanel = {
  visit_id: string;
  context: ContextItem[];
  /** `[label, why]` for sources that exist but produced nothing. Rendered, so
   *  "no labs scanned" never looks like a source the console forgot to build. */
  absent: [string, string][];
  /** What the doctor last chose. Null means they have not trimmed — tick
   *  everything. `[]` means they unticked every line, which is a real choice. */
  include: string[] | null;
  suggestions: string[];
  turns: ResearchTurn[];
  budget: Budget;
  enabled: boolean;
};

export type AskResult = { turn: ResearchTurn; budget: Budget };

/** Thrown when the chain is down (503) or the day's turns are spent (429).
 *  Separate from a generic error because the panel says something different for
 *  each, and neither is a bug the doctor should see a stack-shaped message for. */
export class ResearchUnavailable extends Error {
  readonly kind: "provider" | "budget";
  constructor(message: string, kind: "provider" | "budget") {
    super(message);
    this.name = "ResearchUnavailable";
    this.kind = kind;
  }
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

async function detail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    /* keep the fallback */
  }
  return fallback;
}

/** What would be sent, what has been asked, and what is left of today. */
export async function fetchPanel(token: string, visitId: string): Promise<ResearchPanel> {
  const res = await fetch(`${API_BASE}/research/visits/${visitId}`, {
    headers: authHeaders(token),
    cache: "no-store",
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(await detail(res, `research ${res.status}`));
  return res.json();
}

/** Ask. `include` is ids — null sends everything the server assembled. */
export async function ask(
  token: string,
  visitId: string,
  question: string,
  include: string[] | null,
): Promise<AskResult> {
  const res = await fetch(`${API_BASE}/research/visits/${visitId}`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ question, include }),
  });
  if (res.status === 401) throw new AuthError();
  if (res.status === 503) {
    throw new ResearchUnavailable(await detail(res, "the assistant is unavailable"), "provider");
  }
  if (res.status === 429) {
    throw new ResearchUnavailable(await detail(res, "no research turns left today"), "budget");
  }
  if (!res.ok) throw new Error(await detail(res, `research ${res.status}`));
  return res.json();
}

/** Which items are ticked, given what the server remembered.
 *
 *  Null and `[]` are different answers and must stay different all the way up:
 *  null is "this doctor has not touched the trim, so show everything ticked",
 *  and `[]` is "they unticked every line". Collapsing them would send a
 *  patient's whole context at the exact moment the doctor had said not to. */
export function initialSelection(panel: ResearchPanel): string[] {
  if (panel.include === null) return panel.context.map((item) => item.id);
  const known = new Set(panel.context.map((item) => item.id));
  return panel.include.filter((id) => known.has(id));
}
