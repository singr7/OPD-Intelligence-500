// Typed client for the doctor surface (backend app/routes/doctor.py).
//
// Reads only. The console's *actions* are the S8 queue verbs — `callNext` and
// `setEntryState` are imported from the shared queue client, not reimplemented
// here, because S9 deliberately added no doctor-flavoured action endpoints
// (backend app/doctor.py explains why).

import { API_BASE, AuthError } from "@/app/_lib/queue";

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
};

export type Day = {
  doctor_name: string;
  department_key: string;
  department_name: string;
  date: string;
  rows: DayRow[];
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
};

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

export async function fetchDay(token: string, signal?: AbortSignal): Promise<Day> {
  const res = await fetch(`${API_BASE}/doctor/day`, {
    headers: authHeaders(token),
    cache: "no-store",
    signal,
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`day ${res.status}`);
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
