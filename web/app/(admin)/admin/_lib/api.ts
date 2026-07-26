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
export const fetchSlotTemplates = (t: string) => get<Deferred>(t, "/admin/slot-templates");

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
export const fetchProtocolBankDocument = <T,>(t: string, version?: number) =>
  get<T>(t, `/admin/protocol-banks/document${version ? `?version=${version}` : ""}`);
export const saveProtocolBankDraft = (t: string, bank: unknown, notes?: string) =>
  post<BankVersion>(t, "/admin/protocol-banks/draft", { bank, notes });
export const publishProtocolBank = (t: string, version: number) =>
  post<BankVersion>(t, `/admin/protocol-banks/${version}/publish`, {});
