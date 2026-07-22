// Typed client for the kiosk REST surface (app/routes/kiosk.py). The wire shape
// deliberately mirrors the intake tool contract, so this stays thin — one method
// per tool. The kiosk is a V3 client: taps in, nodes out.

import type { Tree as CanonicalTree } from "./tree/types";

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

export const kioskApi = {
  start(input: {
    lang: string;
    chief_complaint: string;
    caregiver: boolean;
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
};
