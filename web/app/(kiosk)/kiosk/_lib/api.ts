// Typed client for the kiosk REST surface (app/routes/kiosk.py). The wire shape
// deliberately mirrors the intake tool contract, so this stays thin — one method
// per tool. The kiosk is a V3 client: taps in, nodes out.

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
    input: { node_id: string; value: unknown; raw_text?: string | null }
  ) {
    return post<AnswerResult>(`/kiosk/${sessionId}/answer`, input);
  },
  finish(sessionId: string) {
    return post<FinishResult>(`/kiosk/${sessionId}/finish`);
  },
  confirm(sessionId: string) {
    return post<ConfirmResult>(`/kiosk/${sessionId}/confirm`);
  },
};
