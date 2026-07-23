// Typed client for the prescription surface (backend app/routes/prescription.py).
//
// There is no `create` here on purpose: the prescription is produced by *signing*
// the note, so the console reads it back rather than asking for it. If a create
// verb ever appears in this file, doc 03 §8's "the signature is what prescribes"
// has been lost.

import { API_BASE, AuthError } from "@/app/_lib/queue";

/** What the dictation said, plus what the page must show about it. */
export type RxMed = {
  /** Exactly what the doctor dictated. Never a formulary name. */
  name: string;
  dose: string | null;
  route: string | null;
  freq: string | null;
  duration: string | null;
  known: boolean;
  /** Acknowledgement unlocked signing; it did not clear this. */
  flagged: boolean;
  flag_reason: string | null;
  schedule: RxSchedule | null;
};

/**
 * The dosing schedule as far as the doctor's words state it.
 *
 * `slots_known` is the whole point: false means the dictation gave a count
 * ("BD") without a time of day, and no sun/moon may be drawn for it.
 */
export type RxSchedule = {
  morning: boolean;
  afternoon: boolean;
  night: boolean;
  per_day: number | null;
  slots_known: boolean;
  source: string;
};

export type Delivery = { at: string; status: string; detail?: string };

export type Prescription = {
  id: string;
  visit_id: string;
  dictation_id: string | null;
  meds: RxMed[];
  delivered_via: Record<string, Delivery>;
};

export type HistoryRow = {
  prescription_id: string;
  visit_id: string;
  date: string;
  med_names: string[];
  flagged_count: number;
};

async function call<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  if (res.status === 401 || res.status === 403) throw new AuthError();
  if (!res.ok) throw new Error((await res.text()) || `${res.status}`);
  return (await res.json()) as T;
}

/** The visit's prescription, or null while the note is unsigned or med-free. */
export function readPrescription(token: string, visitId: string): Promise<Prescription | null> {
  return call<Prescription | null>(token, `/prescriptions/visits/${visitId}`);
}

export function prescriptionHistory(token: string, patientId: string): Promise<HistoryRow[]> {
  return call<HistoryRow[]>(token, `/prescriptions/patients/${patientId}`);
}

export function deliverPrescription(
  token: string,
  id: string,
  channel: "whatsapp" | "sms",
  toCaregiver = false,
): Promise<Prescription> {
  return call<Prescription>(token, `/prescriptions/${id}/deliver`, {
    method: "POST",
    body: JSON.stringify({ channel, to_caregiver: toCaregiver }),
  });
}

/**
 * Open one copy in a new tab for the browser's print dialog.
 *
 * The endpoint needs the staff bearer token, so a plain `<a href>` won't do and
 * the token must not ride in the query string, where it would land in every
 * access log between here and the box. Same shape as the S8 print tab: fetch
 * with auth, open the HTML as a blob, let the browser make the PDF.
 */
export async function openPrintCopy(
  token: string,
  id: string,
  copy: "clinical" | "patient",
  lang?: string,
): Promise<void> {
  const params = new URLSearchParams({ copy });
  if (lang) params.set("lang", lang);
  const res = await fetch(`${API_BASE}/prescriptions/${id}/print?${params.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401 || res.status === 403) throw new AuthError();
  if (!res.ok) throw new Error(`${res.status}`);
  const html = await res.text();
  const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  window.open(url, "_blank", "noopener");
  // Revoke once the new tab has had time to load.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
