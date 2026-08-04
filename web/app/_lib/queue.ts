// Typed client for the queue surface (backend app/routes/queue.py), shared by
// the board (public) and the coordinator console (staff). Thin fetchers over the
// wire models; the board reads, the console reads and mutates.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// The WS lives on the same host as the API; ws:// or wss:// mirrors http/https.
export const WS_URL = API_BASE.replace(/^http/, "ws") + "/queue/ws";

export type Priority = "routine" | "semi" | "urgent";

export type BoardEntry = {
  token_no: number;
  priority: Priority;
  priority_reason: string | null;
  red_flag: boolean;
  patient_name: string | null;
};

export type BoardDept = {
  department_key: string;
  department_name: string;
  doctor_name: string | null;
  now_serving: number | null;
  now_serving_reason: string | null;
  now_serving_name: string | null;
  next: BoardEntry[];
  waiting_count: number;
  est_wait_low: number;
  est_wait_high: number;
};

export type Board = {
  downtime: boolean;
  departments: BoardDept[];
};

export type LinkState = "none" | "candidate" | "confirmed" | "rejected";

export type ConsoleEntry = {
  id: string;
  token_no: number;
  priority: Priority;
  priority_reason: string | null;
  state: "waiting" | "called" | "in_consult" | "done" | "no_show" | "lab_requeue";
  chief_complaint: string | null;
  red_flag_count: number;
  patient_name: string | null;
  // Who is going to see them, and whether a prior file was matched (AR3). Null
  // doctor = the department pool, which is where a kiosk `Skip` and every
  // offline arrival land.
  assigned_doctor_id: string | null;
  assigned_doctor_name: string | null;
  link_state: LinkState;
};

export type ConsoleDept = {
  department_key: string;
  department_name: string;
  doctor_name: string | null;
  entries: ConsoleEntry[];
};

export type Console = {
  downtime: boolean;
  departments: ConsoleDept[];
};

export type ReconEntry = {
  intake_id: string;
  visit_id: string;
  token_no: number | null;
  department_key: string;
  channel: string;
  chief_complaint: string | null;
  red_flag_count: number;
  client_id: string | null;
  completed_at: string | null;
};

// -- public board -------------------------------------------------------------

export async function fetchBoard(signal?: AbortSignal): Promise<Board> {
  const res = await fetch(`${API_BASE}/queue/board`, { signal, cache: "no-store" });
  if (!res.ok) throw new Error(`board ${res.status}`);
  return res.json();
}

export async function fetchDowntime(): Promise<{ active: boolean; since: string | null }> {
  const res = await fetch(`${API_BASE}/queue/downtime`, { cache: "no-store" });
  if (!res.ok) throw new Error(`downtime ${res.status}`);
  return res.json();
}

// -- staff (bearer token) -----------------------------------------------------

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

export async function fetchConsole(token: string, signal?: AbortSignal): Promise<Console> {
  const res = await fetch(`${API_BASE}/queue/console`, {
    headers: authHeaders(token),
    cache: "no-store",
    signal,
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`console ${res.status}`);
  return res.json();
}

export async function callNext(token: string, departmentKey: string): Promise<void> {
  await staffPost(token, "/queue/call-next", { department_key: departmentKey });
}

export async function setEntryState(
  token: string,
  entryId: string,
  state: ConsoleEntry["state"],
): Promise<void> {
  await staffPost(token, `/queue/entries/${entryId}/state`, { state });
}

export async function reorder(
  token: string,
  departmentKey: string,
  orderedIds: string[],
): Promise<void> {
  await staffPost(token, "/queue/reorder", {
    department_key: departmentKey,
    ordered_ids: orderedIds,
  });
}

export async function setDowntime(token: string, active: boolean): Promise<void> {
  await staffPost(token, "/queue/downtime", { active });
}

// -- assignment (AR3) ---------------------------------------------------------
//
// The desk's half of the kiosk staff strip. It is the compensating control for
// every arrival the strip did not settle: a `Skip`, and every visit an offline
// kiosk synced with no roster to pick from.

export type AssignableDoctor = {
  id: string;
  name: string;
  qualification: string | null;
  on_duty: boolean;
};

export type AssignEntryInput = {
  link_candidate?: boolean | null;
  department_key?: string | null;
  doctor_id?: string | null;
};

export type AssignEntryResult = {
  entry_id: string;
  visit_id: string;
  department_key: string;
  department_name: string;
  assigned_doctor_id: string | null;
  assigned_doctor_name: string | null;
  link_state: LinkState;
  token_no: number | null;
  previous_token_no: number | null;
  token_reissued: boolean;
};

export async function fetchAssignable(
  token: string,
  entryId: string,
): Promise<AssignableDoctor[]> {
  const res = await fetch(`${API_BASE}/queue/entries/${entryId}/assignable`, {
    headers: authHeaders(token),
    cache: "no-store",
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`assignable ${res.status}`);
  return res.json();
}

export async function fetchDepartments(
  token: string,
): Promise<{ key: string; name: string }[]> {
  const res = await fetch(`${API_BASE}/queue/departments`, {
    headers: authHeaders(token),
    cache: "no-store",
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`departments ${res.status}`);
  return res.json();
}

export async function assignEntry(
  token: string,
  entryId: string,
  input: AssignEntryInput,
): Promise<AssignEntryResult> {
  const res = await staffPost(token, `/queue/entries/${entryId}/assign`, input);
  return res.json();
}

export async function fetchReconciliation(
  token: string,
): Promise<{ count: number; entries: ReconEntry[] }> {
  const res = await fetch(`${API_BASE}/queue/reconciliation`, {
    headers: authHeaders(token),
    cache: "no-store",
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`reconciliation ${res.status}`);
  return res.json();
}

export type PaperEntryInput = {
  department_key: string;
  token_no: number;
  lang: string;
  chief_complaint?: string;
  patient_name?: string;
  urgent?: boolean;
  urgent_reason?: string;
};

export async function paperEntry(token: string, input: PaperEntryInput): Promise<void> {
  await staffPost(token, "/queue/downtime/paper-entry", input);
}

async function staffPost(token: string, path: string, body: unknown): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res;
}

// -- auth (minimal phone-OTP, reused by S9 doctor login) ----------------------

export class AuthError extends Error {
  constructor() {
    super("not authenticated");
    this.name = "AuthError";
  }
}

export async function requestOtp(phone: string): Promise<{ debug_code?: string }> {
  const res = await fetch(`${API_BASE}/auth/otp/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone }),
  });
  if (!res.ok) throw new Error(`otp request ${res.status}`);
  return res.json();
}

export async function verifyOtp(phone: string, code: string): Promise<{ access_token: string }> {
  const res = await fetch(`${API_BASE}/auth/otp/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone, code }),
  });
  if (!res.ok) throw new Error("Wrong or expired code");
  return res.json();
}
