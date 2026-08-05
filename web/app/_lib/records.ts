// Typed client for the records surface (backend app/routes/records.py).
//
// The coordinator's phone talks to four of these; the doctor's console (M2)
// reads the other three. Same thin-fetcher shape as _lib/queue.ts.

import { API_BASE } from "./queue";

export type DocumentKind =
  | "lab"
  | "histopath"
  | "imaging_report"
  | "discharge"
  | "outside_rx"
  | "other";

export type DocumentStatus =
  | "capturing"
  | "captured"
  | "extracting"
  | "extracted"
  | "summarized"
  | "extraction_failed";

export type WorklistRow = {
  patient_id: string;
  visit_id: string | null;
  token_no: number | null;
  patient_name: string;
  department_name: string;
  state: string;
  document_count: number;
};

export type FlaggedValue = {
  name: string;
  value_text: string;
  unit: string;
  ref_low: string | null;
  ref_high: string | null;
  flag: "normal" | "low" | "high" | "critical_low" | "critical_high" | "unknown";
  ref_source: "printed" | "default" | "none";
  page: number | null;
  confidence: string;
  canonical_value: string | null;
  canonical_unit: string | null;
};

export type Extraction = {
  summary_text: string | null;
  outlier_count: number;
  report_date: string | null;
  values: FlaggedValue[];
  narrative_findings: string[];
  illegible_regions: string[];
  dropped_rows: number;
  verified: boolean;
  verified_at: string | null;
  prompt_refs: string[];
  uses_fallback_ranges: boolean;
};

export type MedicalDocument = {
  id: string;
  patient_id: string;
  visit_id: string | null;
  kind: DocumentKind;
  status: DocumentStatus;
  pages: number;
  created_at: string;
  failure_reason: string | null;
  // Absent, not empty, until a reading exists — "not read yet" and "read,
  // nothing found" are different things and the UI says so differently.
  extraction: Extraction | null;
};

async function call<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* a non-JSON error body is still an error; keep the status */
    }
    throw new Error(String(detail));
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export function scanWorklist(token: string, q = ""): Promise<WorklistRow[]> {
  const query = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
  return call<WorklistRow[]>(`/records/scan/worklist${query}`, token);
}

export function startDocument(
  token: string,
  body: { patient_id: string; visit_id?: string | null; kind: DocumentKind },
): Promise<MedicalDocument> {
  return call<MedicalDocument>("/records/documents", token, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function uploadPage(
  token: string,
  documentId: string,
  blob: Blob,
): Promise<{ document_id: string; page: number; pages: number }> {
  const form = new FormData();
  form.append("file", blob, "page.jpg");
  return call(`/records/documents/${documentId}/pages`, token, {
    method: "POST",
    body: form,
  });
}

export function completeDocument(
  token: string,
  documentId: string,
): Promise<MedicalDocument> {
  return call<MedicalDocument>(`/records/documents/${documentId}/complete`, token, {
    method: "POST",
  });
}

export function retryDocument(
  token: string,
  documentId: string,
): Promise<MedicalDocument> {
  return call<MedicalDocument>(`/records/documents/${documentId}/retry`, token, {
    method: "POST",
  });
}

export function patientDocuments(
  token: string,
  patientId: string,
): Promise<MedicalDocument[]> {
  return call<MedicalDocument[]>(`/records/patients/${patientId}/documents`, token);
}

export function pageUrl(documentId: string, page: number): string {
  return `${API_BASE}/records/documents/${documentId}/pages/${page}`;
}

export const KIND_LABELS: Record<DocumentKind, string> = {
  lab: "Lab report",
  histopath: "Biopsy / histopath",
  imaging_report: "Imaging report",
  discharge: "Discharge summary",
  outside_rx: "Outside prescription",
  other: "Other",
};

// Longest edge, in pixels, after downscaling on the phone. Enough for a lab
// table to stay legible to a vision model; small enough that a four-page report
// is a megabyte or two on OPD wi-fi rather than sixteen.
export const MAX_EDGE = 2000;
export const JPEG_QUALITY = 0.8;

/**
 * Downscale a camera capture before it ever reaches the network.
 *
 * A 12MP phone JPEG is ~4MB and carries no more readable text than a 2000px
 * one. Doing this on the phone rather than the server is what keeps a
 * coordinator's scan under a few seconds on a shared connection — and the
 * backend refuses anything over its own limit regardless, so this is the
 * difference between "fast" and "rejected".
 */
export async function downscale(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) return file;
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close?.();

  return new Promise((resolve) => {
    canvas.toBlob(
      (blob) => resolve(blob ?? file),
      "image/jpeg",
      JPEG_QUALITY,
    );
  });
}
