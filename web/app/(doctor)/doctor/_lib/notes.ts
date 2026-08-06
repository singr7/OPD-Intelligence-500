// Typed client for the ambient consult note (backend app/routes/notes.py).
//
// Mirrors the dictation client's split into verbs for the same reason: keeping
// `start` separate from `map` is what lets a failed mapping keep the recording.
//
// **There is no medication type in this file, and no `sign`.** That is not an
// omission — a note maps prose into S/O/A/P and produces nothing. If a future
// change wants a `Med[]` here, the thing it actually wants is the Consult tab.

import { API_BASE, AuthError } from "@/app/_lib/queue";

export type Symptom = {
  name: string;
  /** The grade the doctor **said**. Null means unsaid, never mild. */
  grade_mentioned: string | null;
};

export type NoteTags = {
  problems: string[];
  symptoms: Symptom[];
  followups: string[];
};

export type NoteFields = {
  subjective: string;
  objective: string;
  assessment: string;
  plan_narrative: string;
  tags: NoteTags;
};

export type ClinicalNote = {
  id: string;
  visit_id: string;
  status: "draft" | "confirmed";
  transcript: string | null;
  /** What the model produced — frozen, so the review can show what changed. */
  mapped: NoteFields | null;
  /** What the note says now, after the doctor's edits. */
  fields: NoteFields | null;
  edits: { at: string; by: string; field: string }[];
  model: string | null;
  prompt_ref: string | null;
  mapping_error: string | null;
  mapped_at: string | null;
  confirmed_at: string | null;
  created_at: string;
};

export const EMPTY_TAGS: NoteTags = { problems: [], symptoms: [], followups: [] };

export const EMPTY_FIELDS: NoteFields = {
  subjective: "",
  objective: "",
  assessment: "",
  plan_narrative: "",
  tags: EMPTY_TAGS,
};

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

async function unwrap(res: Response): Promise<ClinicalNote> {
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    let detail = `note ${res.status}`;
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

/** Every note on this visit, oldest first. A consult may have several. */
export async function fetchNotes(token: string, visitId: string): Promise<ClinicalNote[]> {
  const res = await fetch(`${API_BASE}/notes/visits/${visitId}`, {
    headers: authHeaders(token),
    cache: "no-store",
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`notes ${res.status}`);
  return res.json();
}

/** Open a new note. Not idempotent — each capture is its own observation. */
export async function startNote(
  token: string,
  visitId: string,
  transcript: string,
): Promise<ClinicalNote> {
  return unwrap(
    await fetch(`${API_BASE}/notes/visits/${visitId}`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify({ transcript }),
    }),
  );
}

export async function mapNote(token: string, noteId: string): Promise<ClinicalNote> {
  return unwrap(
    await fetch(`${API_BASE}/notes/${noteId}/map`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

/** Open the fields with no model in the loop — the typed note, and the way out
 *  of a mapping the doctor does not want to wait for. */
export async function composeNote(token: string, noteId: string): Promise<ClinicalNote> {
  return unwrap(
    await fetch(`${API_BASE}/notes/${noteId}/compose`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

export async function correctNote(
  token: string,
  noteId: string,
  patch: Partial<NoteFields>,
): Promise<ClinicalNote> {
  return unwrap(
    await fetch(`${API_BASE}/notes/${noteId}`, {
      method: "PATCH",
      headers: authHeaders(token),
      body: JSON.stringify(patch),
    }),
  );
}

/** "This is what I meant." Locks the note; generates nothing. */
export async function confirmNote(token: string, noteId: string): Promise<ClinicalNote> {
  return unwrap(
    await fetch(`${API_BASE}/notes/${noteId}/confirm`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

/** How many of a visit's notes are still drafts — what the mic badge counts. */
export function draftCount(notes: ClinicalNote[]): number {
  return notes.filter((n) => n.status === "draft").length;
}
