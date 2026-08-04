// Typed client for the kiosk REST surface (app/routes/kiosk.py). The wire shape
// deliberately mirrors the intake tool contract, so this stays thin — one method
// per tool. The kiosk is a V3 client: taps in, nodes out.

import type { Tree as CanonicalTree } from "./tree/types";
import type { SummaryRole } from "./tree/types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type KioskNode = {
  id: string;
  type: "single" | "multi" | "scale" | "number" | "body_map" | "free_voice";
  text: string;
  options: { id: string; text: string; icon: string | null }[];
  min: number | null;
  max: number | null;
  unit: string | null;
  audio: string | null;
  summary_role: SummaryRole | null;
  // How many questions are left on the tree's default path, counting this one —
  // the honest progress number (S-UX.6). Null from an older server.
  remaining?: number | null;
  // True when this node invites a spoken answer: a free-text node always, a tap
  // node only in the closing pair. The mic is drawn from this and nothing else.
  voice_input?: boolean;
};

/** Who the intake is for (S-UX.6). Collected once, on the details screen, before
 *  the clinical walk — so the token slip, the queue and the prescription all name
 *  the same person instead of "Walk-in patient". */
export type PatientDetails = {
  name: string;
  age: number | null;
  sex: "male" | "female" | "other" | null;
  phone: string;
  /** The hospital ID the patient already carries (AR3). Optional in the
   *  strictest sense — it never gates an intake or a token, and the server uses
   *  it only to *suggest* a prior file to a coordinator. */
  externalId: string;
};

export type Dept = { key: string; name: string };

export type StartResult =
  | {
      status: "routed";
      session_id: string;
      lang: string;
      tier: string;
      department: Dept;
      tree_key: string;
      node: KioskNode | null;
      complete: boolean;
    }
  | {
      status: "needs_department";
      departments: Dept[];
      reason: string | null;
    };

export type AnswerResult = {
  ok: boolean;
  node_id: string;
  complete: boolean;
  error: string | null;
  red_flags: { id: string; severity: string }[];
  node: KioskNode | null;
  // Adaptive intake (S-ADAPT.1, doc 11 §2): a spoken clarifying question when a
  // voice answer was too vague to map. Null unless adaptive is on and the answer
  // needs one. `adaptive_exhausted` = voice gave up; the patient should tap.
  clarify?: string | null;
  adaptive_exhausted?: boolean;
  accepted_value?: unknown;
};

export type FinishResult = {
  readback: string;
  summary_md: string | null;
  red_flags: { id: string; severity: string }[];
  complete: boolean;
};

export type ConfirmResult = {
  token_no: number | null;
  department: Dept | null;
  red_flags: { id: string; severity: string }[];
  cost_inr: string | null;
};

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(res.status, detail || res.statusText);
  }
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

// -- offline surface (S7) -----------------------------------------------------

export type BundleResult = {
  etag: string;
  generated_at: string;
  departments: Dept[];
  trees: { department_key: string | null; tree: CanonicalTree }[];
};

export type LeaseResult = {
  kiosk_id: string;
  date: string;
  blocks: {
    department: Dept;
    start_no: number;
    end_no: number;
    used_up_to: number | null;
    next_free: number;
  }[];
};

export type SyncBody = {
  kiosk_id: string;
  intakes: {
    client_id: string;
    department_key: string;
    tree_key: string;
    lang: string;
    token_no: number;
    answers: unknown;
    chief_complaint: string | null;
    caregiver: boolean;
    patient_name: string;
    patient_age: number | null;
    patient_sex: string | null;
    patient_phone: string | null;
    patient_external_id: string | null;
    completed_at: string;
  }[];
};

export type SyncResponse = {
  results: {
    client_id: string;
    status: "synced" | "duplicate" | "rejected";
    token_no: number | null;
    red_flags: { id: string; severity: string }[];
    error: string | null;
  }[];
  synced: number;
  duplicates: number;
  rejected: number;
};

// -- staff strip (AR3) --------------------------------------------------------
//
// Everything below the `staffHolders` call is PIN-gated server-side by
// `require_kiosk_staff`. The token it returns is deliberately narrow — it opens
// this strip and nothing else — and it is held in memory by the strip component
// only, never in localStorage: a coordinator's PIN typed in a public corridor
// must not survive the shift, the tab, or the next patient.

export type PinHolder = { id: string; name: string };

export type UnlockResult = { token: string; expires_at: string; name: string };

export type StripCandidate = {
  patient_id: string;
  name: string;
  mrn: string;
  age: number | null;
  sex: string | null;
  external_id: string | null;
  last_visit_on: string | null;
};

export type StripDoctor = {
  id: string;
  name: string;
  qualification: string | null;
  on_duty: boolean;
};

export type StripResult = {
  visit_id: string;
  token_no: number | null;
  department_key: string;
  department_name: string;
  departments: Dept[];
  doctors: StripDoctor[];
  default_doctor_id: string | null;
  assigned_doctor_id: string | null;
  link_state: "none" | "candidate" | "confirmed" | "rejected";
  candidate: StripCandidate | null;
};

export type AssignResult = {
  visit_id: string;
  department_key: string;
  department_name: string;
  assigned_doctor_id: string | null;
  assigned_doctor_name: string | null;
  link_state: string;
  patient_name: string | null;
  token_no: number | null;
  previous_token_no: number | null;
  token_reissued: boolean;
};

async function staffGet<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!res.ok) throw new ApiError(res.status, await res.text().catch(() => res.statusText));
  return res.json() as Promise<T>;
}

async function staffPost<T>(path: string, token: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await res.text().catch(() => res.statusText));
  return res.json() as Promise<T>;
}

export const kioskApi = {
  start(input: {
    lang: string;
    chief_complaint: string;
    caregiver: boolean;
    patient_name: string;
    patient_age: number | null;
    patient_sex: string | null;
    patient_phone: string | null;
    patient_external_id: string | null;
    dept_key?: string;
  }) {
    return post<StartResult>("/kiosk/start", input);
  },
  next(sessionId: string) {
    return fetch(`${API_BASE}/kiosk/${sessionId}/next`).then((r) => r.json());
  },
  answer(
    sessionId: string,
    input: {
      node_id: string;
      value: unknown;
      raw_text?: string | null;
      // How many times this node has been re-asked by voice — the server refuses
      // to clarify past the budget and falls back to taps (doc 11 §5).
      attempt?: number;
    }
  ) {
    return post<AnswerResult>(`/kiosk/${sessionId}/answer`, input);
  },
  finish(sessionId: string) {
    return post<FinishResult>(`/kiosk/${sessionId}/finish`);
  },
  confirm(sessionId: string) {
    return post<ConfirmResult>(`/kiosk/${sessionId}/confirm`);
  },

  // -- offline (S7) -----------------------------------------------------------
  bundle() {
    return fetch(`${API_BASE}/kiosk/bundle`, { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw new ApiError(r.status, r.statusText);
      return r.json() as Promise<BundleResult>;
    });
  },
  leaseBlocks(kioskId: string) {
    return post<LeaseResult>(`/kiosk/blocks/lease?kiosk_id=${encodeURIComponent(kioskId)}`);
  },
  sync(body: SyncBody) {
    return post<SyncResponse>("/kiosk/sync", body);
  },

  // -- staff strip (AR3) ------------------------------------------------------
  /** Who can unlock this kiosk. Unauthenticated by necessity — the strip cannot
   *  ask who you are after you have identified yourself — and it returns names
   *  and opaque ids only. */
  staffHolders() {
    return fetch(`${API_BASE}/kiosk/staff/holders`, { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new ApiError(r.status, r.statusText);
      return r.json() as Promise<PinHolder[]>;
    });
  },
  staffUnlock(input: { user_id: string; pin: string }) {
    return post<UnlockResult>("/kiosk/staff/unlock", input);
  },
  strip(sessionId: string, token: string) {
    return staffGet<StripResult>(`/kiosk/${sessionId}/strip`, token);
  },
  assign(
    sessionId: string,
    token: string,
    input: {
      link_candidate?: boolean | null;
      department_key?: string | null;
      doctor_id?: string | null;
    }
  ) {
    return staffPost<AssignResult>(`/kiosk/${sessionId}/assign`, token, input);
  },
};
