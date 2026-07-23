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

export type MappedFields = {
  diagnosis: string | null;
  treatment_events: TreatmentEvent[];
  meds: Med[];
  advice: string[];
  follow_up: FollowUp;
  unclear: string[];
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

export async function mapFields(token: string, dictationId: string): Promise<Dictation> {
  return unwrap(
    await fetch(`${API_BASE}/dictation/${dictationId}/map`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

export async function correct(
  token: string,
  dictationId: string,
  patch: Partial<MappedFields>,
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

/** The accuracy pass behind Web Speech — and the only path on a browser without it. */
export async function transcribeAudio(
  token: string,
  blob: Blob,
  seconds: number,
): Promise<{ text: string; provider: string; uncertain: boolean }> {
  const form = new FormData();
  form.append("file", blob, "consult.webm");
  form.append("lang", "en");
  form.append("duration_seconds", String(Math.max(0, Math.round(seconds))));
  const res = await fetch(`${API_BASE}/dictation/stt`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error("Speech recognition is unavailable — type the note instead.");
  return res.json();
}
