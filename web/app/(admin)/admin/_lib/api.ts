// Typed client for the admin console (backend app/routes/admin.py, S18). Thin
// fetchers over the wire models; every ₹ field is a string on the wire (costs are
// Decimal server-side and must not round-trip through a JS float — display only,
// never arithmetic).

import { API_BASE, AuthError } from "@/app/_lib/queue";

export { API_BASE };

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

async function get<T>(token: string, path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: authHeaders(token),
    cache: "no-store",
    signal,
  });
  if (res.status === 401) throw new AuthError();
  if (res.status === 403) throw new Error("This console needs an admin account.");
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

async function post<T>(token: string, path: string, body: unknown): Promise<T> {
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
  return res.json();
}

/** PATCH, for the edits where an absent field means "leave it alone" rather than
 *  "blank it" — the hospital identity card and the department editor (AYUR-1). */
async function patch<T>(token: string, path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
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
  return res.json();
}

// -- filters (the five usage_events dimensions) -------------------------------

export type Filters = {
  channel?: string;
  tier?: string;
  purpose?: string;
  model?: string;
  provider?: string;
};

function qs(filters: Filters = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v) p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : "";
}

// -- analytics ----------------------------------------------------------------

export type Live = {
  tokens_per_min: number;
  inr_per_min: string;
  active_sessions_by_tier: Record<string, number>;
  at: string;
};

export type SeriesPoint = {
  at: string;
  tokens_in: number;
  tokens_out: number;
  cached_tokens: number;
  audio_seconds: string;
  cost_inr: string;
};
export type Series = { start: string; end: string; granularity: string; points: SeriesPoint[] };

export type BreakdownRow = {
  provider: string;
  model: string | null;
  purpose: string;
  tokens_in: number;
  tokens_out: number;
  audio_seconds: string;
  calls: number;
  cost_inr: string;
  pct_of_spend: number;
};

export type UnitCost = {
  channel: string | null;
  tier: string | null;
  count: number;
  median_inr: string | null;
  p90_inr: string | null;
};
export type UnitEconomics = {
  per_completed_intake: UnitCost[];
  per_abandoned_intake: UnitCost;
  per_dictation: UnitCost;
  overall_per_intake: UnitCost;
};

export type Anomaly = { kind: string; detail: string; value: string };

export type FunnelRow = {
  channel: string;
  started: number;
  completed: number;
  confirmed: number;
  median_duration_s: number | null;
};
export type Ops = {
  funnel: FunnelRow[];
  tier_downgrades: number;
  intakes_by_lang: Record<string, number>;
};

export type CostGuardChannel = {
  channel: string;
  spent_inr: string;
  budget_inr: string | null;
  fraction: number | null;
  override_tier: string | null;
  status: "ok" | "approaching" | "breached" | "uncapped";
};
export type CostGuard = { enabled: boolean; channels: CostGuardChannel[] };

export type WhatIf = { baseline_inr: string; adjusted_inr: string; delta_inr: string };

export const fetchLive = (t: string, s?: AbortSignal) => get<Live>(t, "/admin/analytics/live", s);
export const fetchSeries = (t: string, f: Filters, granularity = "day") => {
  const p = new URLSearchParams({ granularity });
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
  return get<Series>(t, `/admin/analytics/series?${p.toString()}`);
};
export const fetchBreakdown = (t: string, f: Filters) =>
  get<BreakdownRow[]>(t, `/admin/analytics/breakdown${qs(f)}`);
export const fetchUnitEconomics = (t: string) =>
  get<UnitEconomics>(t, "/admin/analytics/unit-economics");
export const fetchAnomalies = (t: string) => get<Anomaly[]>(t, "/admin/analytics/anomalies");
export const fetchOps = (t: string) => get<Ops>(t, "/admin/analytics/ops");

export type TagCount = { label: string; notes: number };
export type SymptomCount = TagCount & {
  /** Notes where the doctor **said** a grade. Not a count of graded symptoms —
   *  nothing in this system grades one. */
  with_grade: number;
};
export type NoteTags = {
  notes_counted: number;
  drafts_excluded: number;
  problems: TagCount[];
  symptoms: SymptomCount[];
  followups: TagCount[];
  /** Server-supplied. Rendered verbatim rather than restated here, so the
   *  caveat cannot drift from the query that earned it. */
  basis: string;
};
export const fetchNoteTags = (t: string) => get<NoteTags>(t, "/admin/analytics/note-tags");
export const fetchCostGuard = (t: string) => get<CostGuard>(t, "/admin/costguard");
export const clearCostGuard = (t: string, channel: string) =>
  post<{ cleared: boolean }>(t, `/admin/costguard/${channel}/clear`, {});
export type TierMix = {
  channel: string;
  from_tier: string;
  to_tier: string;
  intakes: number;
  from_median_inr: string | null;
  to_median_inr: string | null;
  baseline_inr: string;
  adjusted_inr: string;
  delta_inr: string;
  basis: string;
};
export const runTierMix = (t: string, channel: string, from_tier: string, to_tier: string) =>
  get<TierMix>(
    t,
    `/admin/analytics/tier-mix?channel=${channel}&from_tier=${from_tier}&to_tier=${to_tier}`,
  );

export const runWhatIf = (
  t: string,
  overrides: { provider?: string; model?: string; factor: string }[],
) => post<WhatIf>(t, "/admin/analytics/whatif", { overrides });

// -- editors ------------------------------------------------------------------

export type TreeVersion = {
  id: string;
  key: string;
  version: number;
  status: string;
  department_code: string | null;
  published_at: string | null;
  node_count: number;
};
export const fetchTrees = (t: string) => get<TreeVersion[]>(t, "/admin/trees");
export const publishTree = (t: string, key: string, version: number) =>
  post<TreeVersion>(t, `/admin/trees/${key}/publish?version=${version}`, {});

// The tree document itself — what the visual editor loads, edits and posts back.
// Typed loosely on purpose: the shape is the S4 node schema, the server validates
// it with `app.trees.schema.parse`, and a second half-copy of that schema in
// TypeScript would be a second thing to keep in step.
export const fetchTree = <T,>(t: string, key: string, version: number) =>
  get<T>(t, `/admin/trees/${key}?version=${version}`);
export const saveTreeDraft = (t: string, key: string, tree: unknown) =>
  post<TreeVersion>(t, `/admin/trees/${key}/draft`, { tree });

export type TestRunResult = {
  path: string[];
  complete: boolean;
  red_flags: { id: string; severity: string; label: string }[];
  error: string | null;
};
export const testRunTree = (t: string, tree: unknown, answers: Record<string, unknown>) =>
  post<TestRunResult>(t, "/admin/trees/test-run", { tree, answers });

export type PriceRow = {
  id: string;
  provider: string;
  model: string;
  unit: string;
  price_inr: string;
  effective_from: string;
  notes: string | null;
};
export const fetchPriceBook = (t: string) => get<PriceRow[]>(t, "/admin/price-book");
export const addPriceRow = (
  t: string,
  row: {
    provider: string;
    model: string;
    unit: string;
    price_inr: string;
    effective_from: string;
    notes?: string;
  },
) => post<PriceRow>(t, "/admin/price-book", row);

export type Template = {
  name: string;
  lang: string;
  category: string;
  body: string;
  variables: string[];
};
export const fetchTemplates = (t: string) => get<Template[]>(t, "/admin/templates");

export type VoicePackClip = {
  tree_key: string;
  node_id: string;
  lang: string;
  clip_name: string | null;
  recorded: boolean;
};
export const fetchVoicePacks = (t: string) => get<VoicePackClip[]>(t, "/admin/voice-packs");

export type Deferred = { deferred: boolean; arrives_in: string; reason: string };

// -- people + roster (S-GL.2, doc 03 §2/§10) ----------------------------------
//
// Deliberately not versioned, unlike the trees, the protocol bank and the channel
// document: a doctor is not authored content with a review cycle. See the note at
// the top of `backend/app/people.py`.

export type Person = {
  user_id: string;
  name: string;
  phone: string;
  role: string;
  lang: string;
  active: boolean;
  last_login_at: string | null;
  doctor_id: string | null;
  reg_no: string | null;
  qualification: string | null;
  department_code: string | null;
  department_name: string | null;
  clinics: number;
  upcoming_appointments: number;
};

export type Booked = {
  appointment_id: string;
  patient_name: string;
  patient_phone: string;
  at: string;
  slot_type: string | null;
};

export type DeactivationImpact = {
  user_id: string;
  name: string;
  role: string;
  is_doctor: boolean;
  active_clinics: number;
  open_future_slots: number;
  booked: Booked[];
  needs_a_decision: boolean;
};

export type Department = { code: string; name: string };

// -- the facility: hospital identity + departments (AYUR-1, doc 24 §7) --------
//
// The two facts a hospital owns about itself that were previously only editable
// by editing `seeds/hospital.json` on the box. `Department` above is the
// active-only picker the create-a-doctor form uses; `DepartmentRow` is the
// editor's view, and it is the one that sees the closed departments.

export type Hospital = {
  hospital_id: string;
  code: string;
  name: string;
  city: string | null;
  district: string | null;
  default_lang: string;
};

export type DepartmentRow = {
  department_id: string;
  code: string;
  name: string;
  icon: string | null;
  /** The raw stored value. This console is the one surface where the system of
   *  medicine *is* the data rather than something to branch on — every other
   *  consumer reads capability flags (doc 24 §2). */
  care_system: string;
  active: boolean;
  doctors: number;
  published_trees: number;
  /** False means opening this department would send a patient into an error
   *  instead of into questions. The toggle is disabled on it. */
  has_intake: boolean;
};

export type Facility = { hospital: Hospital; departments: DepartmentRow[] };

export type CapabilityChange = {
  flag: string;
  before: string;
  after: string;
  /** The sentence an administrator reads, from the backend's capability
   *  mapping — never composed here, so a third system of medicine needs no
   *  change to this console. */
  label: string;
};

export type CareSystemImpact = {
  code: string;
  name: string;
  from_system: string;
  to_system: string;
  is_a_change: boolean;
  changes: CapabilityChange[];
  doctors: number;
  published_trees: number;
  active: boolean;
};

export const fetchFacility = (t: string) => get<Facility>(t, "/admin/facility");
export const patchHospital = (
  t: string,
  body: { name?: string; city?: string; district?: string; default_lang?: string },
) => patch<Hospital>(t, "/admin/hospital", body);
export const createDepartment = (
  t: string,
  body: { code: string; name: string; icon?: string; care_system?: string; active?: boolean },
) => post<DepartmentRow>(t, "/admin/departments", body);
export const fetchCareSystemImpact = (t: string, code: string, to: string) =>
  get<CareSystemImpact>(
    t,
    `/admin/departments/${encodeURIComponent(code)}/care-system-impact?to=${encodeURIComponent(to)}`,
  );
export const patchDepartment = (
  t: string,
  code: string,
  body: {
    name?: string;
    icon?: string;
    care_system?: string;
    active?: boolean;
    acknowledge?: boolean;
  },
) => patch<DepartmentRow>(t, `/admin/departments/${encodeURIComponent(code)}`, body);

export const fetchPeople = (t: string) => get<Person[]>(t, "/admin/people");
export const fetchDepartments = (t: string) => get<Department[]>(t, "/admin/departments");
export const createStaff = (
  t: string,
  body: { name: string; phone: string; role: string; lang?: string },
) => post<Person>(t, "/admin/people", body);
export const createDoctor = (
  t: string,
  body: {
    name: string;
    phone: string;
    department_code: string;
    reg_no: string;
    qualification?: string;
    lang?: string;
  },
) => post<Person>(t, "/admin/people/doctors", body);
export const invitePerson = (t: string, userId: string) =>
  post<{ sent: boolean; to: string; detail: string }>(t, `/admin/people/${userId}/invite`, {});
export const fetchDeactivationImpact = (t: string, userId: string) =>
  get<DeactivationImpact>(t, `/admin/people/${userId}/deactivation-impact`);
export const deactivatePerson = (t: string, userId: string, acknowledge: boolean) =>
  post<{ clinics_retired: number; slots_blocked: number; appointments_left: Booked[] }>(
    t,
    `/admin/people/${userId}/deactivate`,
    { acknowledge },
  );
export const activatePerson = (t: string, userId: string) =>
  post<Person>(t, `/admin/people/${userId}/activate`, {});

export type Clinic = {
  template_id: string;
  doctor_id: string;
  doctor_name: string;
  reg_no: string;
  department_code: string;
  weekday: number;
  weekday_name: string;
  start: string;
  end: string;
  slot_minutes: number;
  capacity: number;
  slot_type: string;
  active: boolean;
  slots_per_week: number;
  future_slots: number;
  future_booked: number;
  next_dates: string[];
};

export type ClinicWrite = {
  doctor_id: string;
  weekday: number;
  start: string;
  end: string;
  slot_type?: string;
  capacity?: number;
  slot_minutes?: number;
  acknowledge?: boolean;
};

export type ChangeImpact = {
  template_id: string;
  label: string;
  empty_future_slots: number;
  booked: Booked[];
  needs_a_decision: boolean;
};

export type PlannedClinic = {
  line: number;
  doctor_label: string;
  doctor_name: string | null;
  department_code: string | null;
  weekday_name: string;
  start: string;
  end: string;
  slot_type: string;
  capacity: number;
  slot_minutes: number;
  slots_per_week: number;
  action: string;
  error: string | null;
};

export type RosterPlan = {
  ok: boolean;
  counts: Record<string, number>;
  rows: PlannedClinic[];
};

export type ImportResult = {
  created: number;
  updated: number;
  unchanged: number;
  slots_generated: number;
  disturbed: Booked[];
};

export const fetchClinics = (t: string) => get<Clinic[]>(t, "/admin/slot-templates");
export const fetchClinicImpact = (t: string, templateId: string) =>
  get<ChangeImpact>(t, `/admin/slot-templates/${templateId}/impact`);
export const createClinic = (t: string, body: ClinicWrite) =>
  post<Clinic>(t, "/admin/slot-templates", body);
export const updateClinic = (t: string, templateId: string, body: ClinicWrite) =>
  send<Clinic>(t, "PUT", `/admin/slot-templates/${templateId}`, body);
export const retireClinic = (t: string, templateId: string, acknowledge: boolean) =>
  send<ChangeImpact>(
    t,
    "DELETE",
    `/admin/slot-templates/${templateId}?acknowledge=${acknowledge}`,
  );
export const generateSlots = (t: string, body: { doctor_id?: string; days?: number }) =>
  post<{ created: number; start: string; days: number }>(t, "/admin/slots/generate", body);

/** Upload a roster. Dry run by default — the preview and the apply are the same
 *  request with one flag flipped, so what an admin previews is what happens. */
export async function importRoster(
  token: string,
  file: File,
  opts: { dryRun: boolean; acknowledge?: boolean } = { dryRun: true },
): Promise<{ plan: RosterPlan; applied: ImportResult | null }> {
  const form = new FormData();
  form.append("file", file);
  const params = new URLSearchParams({
    dry_run: String(opts.dryRun),
    acknowledge: String(opts.acknowledge ?? false),
  });
  const res = await fetch(`${API_BASE}/admin/roster/import?${params}`, {
    method: "POST",
    // No Content-Type: the browser must set the multipart boundary itself.
    headers: { Authorization: `Bearer ${token}` },
    body: form,
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
  return res.json();
}

// The live protocol bank (doc 03 §9/§10) — the published row if there is one, the
// seed file otherwise. This is the reading view; the editor works on the document
// (`/admin/protocol-banks`), because the validator's guarantees are properties of
// the whole bank rather than of one protocol.
export type ProtocolRung = {
  day_offset: number;
  question_set: string;
  asks_about: string;
  questions: number;
  grading_rules: number;
};
export type ProtocolTemplate = {
  key: string;
  label: string;
  cycle_days: number;
  precedence: number;
  matches: { drug_classes: string[]; keywords: string[] };
  checkins: ProtocolRung[];
};
export type ProtocolQuestionSet = {
  key: string;
  title: string;
  questions: { id: string; type: string; prompt: string }[];
  grading: { id: string; grade: string; reason: string }[];
};
export type ProtocolBank = {
  version: number;
  editable: boolean;
  source: string;
  protocols: ProtocolTemplate[];
  question_sets: ProtocolQuestionSet[];
};
export const fetchProtocolTemplates = (t: string) =>
  get<ProtocolBank>(t, "/admin/protocol-templates");

export type BankVersion = {
  id: string;
  version: number;
  status: string;
  published_at: string | null;
  notes: string | null;
  protocol_count: number;
  question_set_count: number;
};
export const fetchProtocolBanks = (t: string) => get<BankVersion[]>(t, "/admin/protocol-banks");

// -- the switchboard (S-GL.1, doc 12 §1) --------------------------------------
//
// `enabled` and `ready` are two fields rather than one, and `open` is derived
// server-side. A console can switch a channel off; it cannot assert that Meta is
// provisioned when it is not, so readiness comes back computed and read-only.

export type ChannelState = {
  channel: string;
  enabled: boolean;
  ready: boolean;
  open: boolean;
  reason: string;
  ladder: string[];
  max_concurrent: number;
  note: string;
};
export type Channels = {
  channels: ChannelState[];
  kiosk_voice_profile: string;
  voice_profiles: VoiceProfile[];
  max_oss_sessions: number;
  campaign_mix: Record<string, number>;
  from_file: boolean;
  version: number | null;
};
export type VoiceComponent = {
  component: string;
  provider: string;
  model: string;
  configured: boolean;
  tested: boolean;
  healthy: boolean;
  detail: string;
};
export type VoiceProfile = {
  name: string;
  active: boolean;
  ready: boolean;
  reason: string;
  components: VoiceComponent[];
};
export type ChannelVersion = {
  id: string;
  version: number;
  status: string;
  published_at: string | null;
  notes: string | null;
  enabled: Record<string, boolean>;
};

export const fetchChannels = (t: string) => get<Channels>(t, "/admin/channels");
export const fetchChannelVersions = (t: string) =>
  get<ChannelVersion[]>(t, "/admin/channels/versions");
export const fetchChannelDocument = <T,>(t: string, version?: number) =>
  get<T>(t, `/admin/channels/document${version ? `?version=${version}` : ""}`);
export const saveChannelDraft = (t: string, config: unknown, notes?: string) =>
  post<ChannelVersion>(t, "/admin/channels/draft", { config, notes });
export const publishChannels = (t: string, version: number) =>
  post<ChannelVersion>(t, `/admin/channels/${version}/publish`, {});

// Credentials are write-only over the wire: there is no fetcher that returns one,
// and there must never be. `configured`, `missing` and `last_test` are the whole
// of what this console is ever told about a vendor's secrets.
export type ProviderCredential = {
  provider: string;
  configured: boolean;
  missing: string[];
  source: string;
  updated_at: string | null;
  last_test: { ok?: boolean; at?: string; detail?: string };
  derived_key: boolean;
  unreadable: boolean;
  fields: string[];
};
export type ProviderTest = { ok: boolean; at: string; detail: string };

export const fetchProviderCredentials = (t: string) =>
  get<ProviderCredential[]>(t, "/admin/providers/credentials");
export const testProvider = (t: string, name: string, component?: string) =>
  post<ProviderTest>(
    t,
    `/admin/providers/${name}/test${component ? `?component=${component}` : ""}`,
    {},
  );

// PUT and DELETE, which the two helpers above do not cover: a credential is
// replaced or removed, never appended to.
async function send<T>(token: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: authHeaders(token),
    body: body === undefined ? undefined : JSON.stringify(body),
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
  return res.status === 204 ? (undefined as T) : res.json();
}

export const setProviderCredentials = (
  t: string,
  name: string,
  values: Record<string, string>,
) => send<ProviderCredential>(t, "PUT", `/admin/providers/${name}/credentials`, { values });
export const clearProviderCredentials = (t: string, name: string) =>
  send<void>(t, "DELETE", `/admin/providers/${name}/credentials`);
export const fetchProtocolBankDocument = <T,>(t: string, version?: number) =>
  get<T>(t, `/admin/protocol-banks/document${version ? `?version=${version}` : ""}`);
export const saveProtocolBankDraft = (t: string, bank: unknown, notes?: string) =>
  post<BankVersion>(t, "/admin/protocol-banks/draft", { bank, notes });
export const publishProtocolBank = (t: string, version: number) =>
  post<BankVersion>(t, `/admin/protocol-banks/${version}/publish`, {});
