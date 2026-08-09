// Typed client for the doctor surface (backend app/routes/doctor.py).
//
// Two reads and two writes. The console's *queue* actions are still the S8 verbs
// — `callNext` and `setEntryState` are imported from the shared queue client,
// not reimplemented here, because there are no doctor-flavoured copies of the
// queue state machine (backend app/doctor.py explains why).
//
// Neither write is a queue transition. `takePatient` changes who the visit
// belongs to, not where it sits in the line; `concludeVisit` records how the
// consult ended — including the two endings that leave this system with no
// prescription in it — and lets the queue verb do the queue's part.

import type { CapabilitiesPayload } from "@/app/_lib/careSystem";
import { API_BASE, AuthError } from "@/app/_lib/queue";

/** The three worklists. `mine` is the default because the kiosk now assigns
 *  essentially every arrival (AR3); `unassigned` is the safety net for the ones
 *  it did not — a kiosk `Skip`, and every offline arrival. */
export type DayScope = "mine" | "unassigned" | "department";

export const DAY_SCOPES: DayScope[] = ["mine", "unassigned", "department"];

export type DayCounts = {
  mine: number;
  unassigned: number;
  department: number;
  /** Unassigned *and still waiting* — the coordinator console's exact
   *  definition, and what drives the rail's attention state. */
  unassigned_waiting: number;
  /** Everyone in the department still waiting, whatever scope is open. "The
   *  line outside" — a figure that shrank when the doctor switched to their own
   *  list would answer a different question than the one being asked. */
  waiting: number;
};

export type DayRow = {
  entry_id: string;
  visit_id: string;
  token_no: number;
  state: "waiting" | "called" | "in_consult" | "done" | "no_show" | "lab_requeue";
  priority: "routine" | "semi" | "urgent";
  priority_reason: string | null;
  patient_name: string;
  patient_age: number | null;
  patient_sex: string | null;
  chief_complaint: string | null;
  red_flag_count: number;
  called_at: string | null;
  assigned_doctor_id: string | null;
  assigned_doctor_name: string | null;
  is_mine: boolean;
};

export type Day = {
  doctor_name: string;
  doctor_id: string;
  department_key: string;
  department_name: string;
  date: string;
  scope: DayScope;
  counts: DayCounts;
  rows: DayRow[];
  /** Which console this doctor gets (doc 24 §6), derived server-side from
   *  their department's system of medicine and delivered as flags — never as
   *  the system's name, so no component can branch on it. The day is the
   *  console's bootstrap: it lands before any patient is opened, so the tabs
   *  know what they are before there is anything to draw in them.
   *
   *  Widen it with `fromPayload` from `@/app/_lib/careSystem`; that adapter is
   *  what makes a flag added server-side and forgotten here fail to compile
   *  rather than arrive as `undefined` and silently hide a section. */
  capabilities: CapabilitiesPayload;
};

export type RedFlag = {
  id: string;
  severity: "routine" | "semi" | "urgent";
  label: string;
  instruction: string;
  source_node: string | null;
};

export type AnswerRow = {
  node_id: string;
  question: string;
  answer: string;
  said: string | null;
  flagged: boolean;
};

export type TimelineVisit = {
  visit_id: string;
  date: string;
  department_name: string;
  status: string;
  token_no: number | null;
  chief_complaint: string | null;
  is_current: boolean;
};

export type Trend = {
  symptom: string;
  points: { at: string; value: number }[];
};

export type Summary = {
  chief_concern: string | null;
  hpi: string[];
  symptoms: Record<string, string>[];
  history_meds: string[];
  since_last_visit: string[];
  patient_words: Record<string, string>;
  unclear: string[];
};

export type PatientCard = {
  patient_id: string;
  visit_id: string;
  intake_id: string | null;
  mrn: string;
  name: string;
  age: number | null;
  sex: string | null;
  lang: string;
  village: string | null;
  phone: string;
  token_no: number | null;
  department_name: string;
  visit_date: string;
  entry_id: string | null;
  entry_state: string | null;
  chief_complaint: string | null;
  chief_complaint_en: string | null;
  summary: Summary;
  summary_md: string | null;
  red_flags: RedFlag[];
  answers: AnswerRow[];
  timeline: TimelineVisit[];
  trends: Trend[];
  tier: string | null;
  intake_lang: string | null;
  completed_at: string | null;
  assigned_doctor_id: string | null;
  assigned_doctor_name: string | null;
  diagnosis: Diagnosis | null;
  /** Whether a family member answered instead of the patient. Part of the
   *  provenance line that replaced the old confidence percentage. */
  caregiver_answered: boolean;
  /** How the consult ended, once a doctor has said. Null is "not concluded",
   *  which is not the same fact as "nothing was prescribed" and must not be
   *  rendered as one. */
  rx_mode: RxMode | null;
  concluded_at: string | null;
  conclusion_note: string | null;
  /** Whether this visit already has a signed note — read from the record rather
   *  than remembered per session, so a reload does not forget it. */
  note_signed: boolean;
  /** What this record knows about what she reacts to (SESSION-ALLERGY). */
  allergies: AllergyView;
};

/** One statement somebody made about this patient's allergies, with the
 *  provenance that makes it readable. Every surface that renders one states who
 *  said it and when; the shape carries that rather than leaving it optional. */
export type AllergyEntry = {
  id: string;
  kind: "substance" | "none_known";
  substance: string | null;
  substance_en: string | null;
  reaction: string | null;
  severity: "unknown" | "mild" | "severe";
  source: "patient_kiosk" | "caregiver_kiosk" | "doctor";
  stated_at: string;
  confirmed_at: string | null;
  confirmed_by_name: string | null;
  recorded_by_name: string | null;
  retracted_at: string | null;
  retracted_by_name: string | null;
  retracted_reason: string | null;
};

/** The three states, and `state` is the only thing to branch on.
 *
 *  They must never collapse into each other:
 *
 *    never_asked   nobody has asked this patient. Not "no known allergies".
 *    none_stated   somebody asked and was told none — rendered with its source
 *                  and date, never bare.
 *    known         one or more live substances.
 *
 *  The server derives it (`app.allergies.for_patient`); nothing here re-derives
 *  it from `entries`, because two implementations of this would eventually
 *  disagree about the most safety-critical line on the screen. */
export type AllergyView = {
  state: "never_asked" | "none_stated" | "known";
  entries: AllergyEntry[];
  none_statement: AllergyEntry | null;
  retracted: AllergyEntry[];
};

/** How a consult ended, prescribing-wise. Two of the three leave this system
 *  with no prescription in it, which is exactly why they are stated. */
export type RxMode = "system" | "external_manual" | "none";

export type Conclusion = {
  visit_id: string;
  rx_mode: RxMode;
  concluded_at: string;
  conclusion_note: string | null;
  entry_state: string | null;
};

/** The working diagnosis and where it came from. `on` is the date of the visit
 *  whose *signed* note carried it — the spine states it, because an unqualified
 *  diagnosis line silently belonging to a note from March is worse than none. */
export type Diagnosis = {
  text: string;
  on: string;
  is_current_visit: boolean;
};

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

export async function fetchDay(
  token: string,
  scope: DayScope = "mine",
  signal?: AbortSignal,
): Promise<Day> {
  const res = await fetch(`${API_BASE}/doctor/day?scope=${scope}`, {
    headers: authHeaders(token),
    cache: "no-store",
    signal,
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`day ${res.status}`);
  return res.json();
}

/** "I'll see this one." Works on an unassigned patient and on a colleague's. */
export async function takePatient(token: string, visitId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/doctor/visits/${visitId}/take`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `take ${res.status}`);
  }
}

/**
 * Close the consult, saying how it ended.
 *
 * `external_manual` and `none` are the two that leave nothing digital behind,
 * and recording them is the whole point: a visit that simply stops is
 * indistinguishable from one the doctor was interrupted in the middle of.
 */
export async function concludeVisit(
  token: string,
  visitId: string,
  rxMode: RxMode,
  note?: string,
): Promise<Conclusion> {
  const res = await fetch(`${API_BASE}/doctor/visits/${visitId}/conclude`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ rx_mode: rxMode, note: note?.trim() || null }),
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(typeof detail?.detail === "string" ? detail.detail : `conclude ${res.status}`);
  }
  return res.json();
}

/** Record an allergy, or record that the doctor asked and there are none.
 *
 *  Every one of these three returns the whole recomputed view rather than the
 *  row it touched, so the console re-renders from the server's derivation
 *  instead of patching its own copy. */
export async function recordAllergy(
  token: string,
  visitId: string,
  input: {
    substance?: string | null;
    reaction?: string | null;
    severity?: "unknown" | "mild" | "severe";
    none_known?: boolean;
  },
): Promise<AllergyView> {
  return allergyWrite(token, `/doctor/visits/${visitId}/allergies`, input);
}

/** Stand behind a statement somebody else made — usually the patient's own. */
export async function confirmAllergy(
  token: string,
  visitId: string,
  allergyId: string,
): Promise<AllergyView> {
  return allergyWrite(token, `/doctor/visits/${visitId}/allergies/${allergyId}/confirm`, {});
}

/** Withdraw a statement. The row stays on file, struck out, with a name on it. */
export async function retractAllergy(
  token: string,
  visitId: string,
  allergyId: string,
  reason?: string,
): Promise<AllergyView> {
  return allergyWrite(token, `/doctor/visits/${visitId}/allergies/${allergyId}/retract`, {
    reason: reason?.trim() || null,
  });
}

async function allergyWrite(
  token: string,
  path: string,
  body: unknown,
): Promise<AllergyView> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(typeof detail?.detail === "string" ? detail.detail : `allergy ${res.status}`);
  }
  return res.json();
}

export async function fetchPatient(
  token: string,
  visitId: string,
  signal?: AbortSignal,
): Promise<PatientCard> {
  const res = await fetch(`${API_BASE}/doctor/patients/${visitId}`, {
    headers: authHeaders(token),
    cache: "no-store",
    signal,
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`patient ${res.status}`);
  return res.json();
}
