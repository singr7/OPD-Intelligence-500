// Typed client for the dictation surface (backend app/routes/dictation.py).
//
// Mirrors the backend's deliberate split into verbs: `start` stores the
// transcript, `mapFields` runs the model, `correct` records the doctor's fixes,
// `sign` locks it. The split is what lets a failed mapping keep the recording,
// so the client keeps it too rather than collapsing them into one save.

import { API_BASE, AuthError } from "@/app/_lib/queue";

export type Suggestion = { name: string; generic: string; score: number };

export type Med = {
  /** Exactly what the doctor said. The UI must never substitute a formulary name here. */
  name: string;
  dose: string | null;
  route: string | null;
  freq: string | null;
  duration: string | null;
  as_spoken: string;
  known: boolean;
  generic: string | null;
  drug_class: string | null;
  ambiguous: boolean;
  /** Fuzzy neighbours from the formulary. Advice for the doctor — never a value. */
  suggestions: Suggestion[];
  /** The name is not in the transcript — the model renamed or invented it. */
  unsaid: boolean;
  acknowledged: boolean;
};

export type TreatmentEvent = {
  cycle: number | null;
  regimen: string;
  date: string | null;
  next_due: string | null;
  as_spoken: string;
};

export type FollowUp = { when: string | null; as_spoken: string; instructions: string };

/** The doctor's own ayurvedic assessment (doc 24 §6.1).
 *
 *  Five free-text lines, and every one of them is typed by the doctor: no model
 *  writes these, the prompts are told never to infer a prakriti or a dosha, and
 *  there is no server path that fills them from a mapping. Optional on the wire
 *  so a note stored before doc 24 parses. */
export type Assessment = {
  prakriti: string;
  vikriti: string;
  agni: string;
  koshtha: string;
  nidana: string;
};

export type MappedFields = {
  diagnosis: string | null;
  treatment_events: TreatmentEvent[];
  meds: Med[];
  advice: string[];
  follow_up: FollowUp;
  unclear: string[];
  assessment?: Assessment;
  /** Diet & lifestyle lines (doc 24 §6.2). Kept apart from `advice` because they
   *  print under their own heading, and in an ayurveda consult they are half of
   *  the treatment rather than a footnote to it. */
  pathya_apathya?: string[];
};

/** An assessment with every line blank — what a note that has none looks like,
 *  and the starting point for one a doctor is about to fill in. */
export const EMPTY_ASSESSMENT: Assessment = {
  prakriti: "",
  vikriti: "",
  agni: "",
  koshtha: "",
  nidana: "",
};

export type Dictation = {
  id: string;
  visit_id: string;
  status: "draft" | "signed";
  transcript: string | null;
  /** What the model produced — frozen, so the review can diff against it. */
  mapped: MappedFields | null;
  /** What the record says now, after corrections. */
  fields: MappedFields | null;
  edits: { at: string; by: string; field: string }[];
  model: string | null;
  prompt_ref: string | null;
  mapping_error: string | null;
  mapped_at: string | null;
  signed_at: string | null;
  /** Flagged drugs still blocking the signature. */
  blocking_meds: string[];
};

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

async function unwrap(res: Response): Promise<Dictation> {
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    let detail = `dictation ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep the status-code message */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function fetchDictation(token: string, visitId: string): Promise<Dictation | null> {
  const res = await fetch(`${API_BASE}/dictation/visits/${visitId}`, {
    headers: authHeaders(token),
    cache: "no-store",
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`dictation ${res.status}`);
  return res.json();
}

export async function startDictation(
  token: string,
  visitId: string,
  transcript: string,
): Promise<Dictation> {
  return unwrap(
    await fetch(`${API_BASE}/dictation/visits/${visitId}`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify({ transcript }),
    }),
  );
}

/**
 * "Type note": open the editable fields with no model in the loop.
 *
 * The same record, the same corrections trail, the same signature and the same
 * refusal on a drug the formulary does not know. Speech is an input method here,
 * not a prerequisite for prescribing — which is what it was until now.
 */
export async function composeNote(token: string, dictationId: string): Promise<Dictation> {
  return unwrap(
    await fetch(`${API_BASE}/dictation/${dictationId}/compose`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

export async function mapFields(token: string, dictationId: string): Promise<Dictation> {
  return unwrap(
    await fetch(`${API_BASE}/dictation/${dictationId}/map`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

/** What "tap to fix" may send.
 *
 *  `Partial<MappedFields>` everywhere except `assessment`, which is a
 *  **partial of a partial**: the five lines are edited one at a time and the
 *  server merges them by key, so a commit carries only the line that changed.
 *  Sending the whole object would spread the other four from state one round
 *  trip old and blank whichever the doctor filled in just before this one. */
export type NotePatch = Omit<Partial<MappedFields>, "assessment"> & {
  assessment?: Partial<Assessment>;
};

export async function correct(
  token: string,
  dictationId: string,
  patch: NotePatch,
): Promise<Dictation> {
  return unwrap(
    await fetch(`${API_BASE}/dictation/${dictationId}`, {
      method: "PATCH",
      headers: authHeaders(token),
      body: JSON.stringify(patch),
    }),
  );
}

export async function signDictation(token: string, dictationId: string): Promise<Dictation> {
  return unwrap(
    await fetch(`${API_BASE}/dictation/${dictationId}/sign`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

// The STT call moved to `useVoiceCapture` in M4, where the recorder that makes
// the recording now lives. Both doctor surfaces post to it; they differ only in
// which endpoint, and therefore which meter, receives the clip.
