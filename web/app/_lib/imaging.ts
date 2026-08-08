// Typed client for the PACS stub (backend app/routes/imaging.py).
//
// **`state` is the payload, not `studies.length`.** A list with nothing in it
// means four different things, and this file exists mostly to keep them four:
// the PACS answered and had nothing, the PACS could not be reached, the patient
// has no UHC ID to look up, or imaging is switched off here. Only the first is
// a fact about the patient. Any code that renders "no imaging on file" from an
// empty array has told a doctor something the server never said.
//
// There is no viewer URL built here either — the server sends one ready-made
// per study, so the console never learns the viewer's shape and cannot be
// talked into composing a different one.

import { API_BASE, AuthError } from "@/app/_lib/queue";

export type ImagingState = "ok" | "unreachable" | "no_uhc_id" | "disabled";

export type Study = {
  study_uid: string;
  /** Null when the study carried no StudyDate. Rendered as "date not
   *  recorded", never defaulted — a decade-old scan must not sort to today. */
  study_date: string | null;
  modality: string;
  description: string;
  series_count: number | null;
  /** Built server-side. Empty when no viewer is configured. */
  viewer_url: string;
};

export type ImagingLookup = {
  state: ImagingState;
  studies: Study[];
  /** The PACS AE title, for the line that helps whoever debugs a missing
   *  study. Not a secret and not clinical. */
  aet: string;
};

/** What the console shows when it has not asked yet. Deliberately not
 *  `state: "ok"` with an empty list, which would claim the PACS answered. */
export const IMAGING_UNASKED: ImagingLookup = {
  state: "disabled",
  studies: [],
  aet: "",
};

export async function patientStudies(token: string, visitId: string): Promise<ImagingLookup> {
  const res = await fetch(`${API_BASE}/imaging/visits/${visitId}/studies`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`imaging ${res.status}`);
  return res.json();
}

/** Where the report opens. The backend streams it inline. */
export function reportUrl(visitId: string, studyUid: string): string {
  return `${API_BASE}/imaging/visits/${visitId}/studies/${studyUid}/report`;
}

/** One line for the context spine, or null when there is nothing worth saying.
 *
 *  Null for `disabled` on purpose: an installation with no PACS should not
 *  carry a permanent "imaging: switched off" line on every patient's spine —
 *  that is an operator's fact, not a clinical one, and the spine is expensive
 *  space. Every other state gets a line, including "unreachable", which is
 *  precisely when a doctor most needs to know not to trust the absence. */
export function imagingSpineLine(lookup: ImagingLookup): string | null {
  switch (lookup.state) {
    case "disabled":
      return null;
    case "no_uhc_id":
      return "no UHC ID — imaging cannot be looked up";
    case "unreachable":
      return "imaging unreachable — not checked";
    case "ok":
      // "studies", never "scans". This clause sits directly after the scanned-
      // paper tally on the same spine line, and the first screenshot read
      // "nothing scanned for this patient · 2 scans" — two senses of the same
      // root word, forty pixels apart, which parses as a contradiction before
      // it parses as two facts.
      return lookup.studies.length === 0
        ? "no imaging on file"
        : `${lookup.studies.length} imaging stud${lookup.studies.length === 1 ? "y" : "ies"}`;
  }
}
