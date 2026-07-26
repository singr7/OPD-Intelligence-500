"use client";

// Tab 1 — Cost & tokens (doc 03 §11). Live strip, cost-guard status, a per-day
// spend sparkline with the five generic filters, the provider→model→purpose
// breakdown, unit-economics cards, anomalies, and the what-if recompute. Every ₹
// is displayed as its wire string — no arithmetic on money in the browser.

import { useState } from "react";
import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

const CHANNELS = ["", "kiosk", "phone", "whatsapp", "app", "paper"];
const TIERS = ["", "conversational", "rule_based", "prerecorded", "paper"];
const PURPOSES = ["", "intake_turn", "summary", "routing", "dictation", "checkin", "other"];

export function CostTab({ token, onError }: TabProps) {
  const [filters, setFilters] = useState<api.Filters>({});
  const live = useLoad(() => api.fetchLive(token), onError);
  const guard = useLoad(() => api.fetchCostGuard(token), onError);
  const series = useLoad(() => api.fetchSeries(token, filters, "day"), onError, [
    filters.channel,
    filters.tier,
    filters.purpose,
    filters.model,
    filters.provider,
  ]);
  const breakdown = useLoad(() => api.fetchBreakdown(token, filters), onError, [
    filters.channel,
    filters.tier,
    filters.purpose,
  ]);
  const ue = useLoad(() => api.fetchUnitEconomics(token), onError);
  const anomalies = useLoad(() => api.fetchAnomalies(token), onError);

  const set = (k: keyof api.Filters, v: string) =>
    setFilters((f) => ({ ...f, [k]: v || undefined }));

  const maxCost = Math.max(
    1,
    ...(series.data?.points.map((p) => Number(p.cost_inr)) ?? [0]),
  );

  return (
    <>
      {/* live strip */}
      <section className="cards">
        <div className="card">
          <div className="label">Tokens / min</div>
          <div className="value">{live.data?.tokens_per_min ?? "—"}</div>
          <div className="sub">trailing 60 seconds</div>
        </div>
        <div className="card">
          <div className="label">₹ / min</div>
          <div className="value">₹{live.data?.inr_per_min ?? "—"}</div>
          <div className="sub">right now</div>
        </div>
        <div className="card">
          <div className="label">Active voice sessions</div>
          <div className="value">
            {live.data
              ? Object.values(live.data.active_sessions_by_tier).reduce((a, b) => a + b, 0)
              : "—"}
          </div>
          <div className="sub">
            {live.data
              ? Object.entries(live.data.active_sessions_by_tier)
                  .map(([t, n]) => `${n} ${t}`)
                  .join(" · ") || "none"
              : ""}
          </div>
        </div>
      </section>

      {/* cost guard */}
      <section>
        <h2>Today’s spend vs budget</h2>
        {guard.data && !guard.data.enabled && (
          <p className="muted">Cost guard is off — channels run at their configured tier.</p>
        )}
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th className="num">Spent</th>
              <th className="num">Budget</th>
              <th style={{ width: 180 }}>Usage</th>
              <th>Status</th>
              <th>Forced tier</th>
            </tr>
          </thead>
          <tbody>
            {guard.data?.channels.map((c) => {
              const frac = c.fraction ?? 0;
              const cls = frac >= 1 ? "bad" : frac >= 0.8 ? "warn" : "";
              return (
                <tr key={c.channel}>
                  <td>{c.channel}</td>
                  <td className="num">₹{c.spent_inr}</td>
                  <td className="num">{c.budget_inr ? `₹${c.budget_inr}` : "—"}</td>
                  <td>
                    <div className={`bar ${cls}`}>
                      <span style={{ width: `${Math.min(100, frac * 100)}%` }} />
                    </div>
                  </td>
                  <td>
                    <span className={`pill ${c.status}`}>{c.status}</span>
                  </td>
                  <td>
                    {c.override_tier ? (
                      <button
                        className="ghost"
                        onClick={async () => {
                          try {
                            await api.clearCostGuard(token, c.channel);
                            guard.reload();
                          } catch (e) {
                            onError(e);
                          }
                        }}
                      >
                        {c.override_tier} — clear
                      </button>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {/* filters + sparkline */}
      <section>
        <h2>Spend over time</h2>
        <div className="row">
          <Select label="Channel" value={filters.channel ?? ""} opts={CHANNELS} onChange={(v) => set("channel", v)} />
          <Select label="Tier" value={filters.tier ?? ""} opts={TIERS} onChange={(v) => set("tier", v)} />
          <Select label="Purpose" value={filters.purpose ?? ""} opts={PURPOSES} onChange={(v) => set("purpose", v)} />
        </div>
        {series.data && series.data.points.length > 0 ? (
          <div className="spark" title="daily ₹ spend">
            {series.data.points.map((p) => (
              <i
                key={p.at}
                style={{ height: `${(Number(p.cost_inr) / maxCost) * 100}%` }}
                title={`${p.at.slice(0, 10)}: ₹${p.cost_inr}`}
              />
            ))}
          </div>
        ) : (
          <p className="muted">No usage in this window.</p>
        )}
      </section>

      {/* breakdown */}
      <section>
        <h2>Where the money goes</h2>
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th>Purpose</th>
              <th className="num">Tokens</th>
              <th className="num">Audio s</th>
              <th className="num">Calls</th>
              <th className="num">₹</th>
              <th className="num">% spend</th>
            </tr>
          </thead>
          <tbody>
            {breakdown.data?.map((r, i) => (
              <tr key={i}>
                <td>{r.provider}</td>
                <td>{r.model ?? "—"}</td>
                <td>{r.purpose}</td>
                <td className="num">{r.tokens_in + r.tokens_out}</td>
                <td className="num">{r.audio_seconds}</td>
                <td className="num">{r.calls}</td>
                <td className="num">₹{r.cost_inr}</td>
                <td className="num">{r.pct_of_spend}%</td>
              </tr>
            ))}
            {breakdown.data?.length === 0 && (
              <tr>
                <td colSpan={8} className="muted">
                  No usage in this window.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {/* unit economics */}
      <section>
        <h2>Unit economics</h2>
        <div className="cards">
          <UeCard title="₹ / completed intake" u={ue.data?.overall_per_intake} />
          <UeCard title="₹ / abandoned intake" u={ue.data?.per_abandoned_intake} />
          <UeCard title="₹ / dictation → Rx" u={ue.data?.per_dictation} />
        </div>
        {ue.data && ue.data.per_completed_intake.length > 0 && (
          <table style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Channel</th>
                <th>Tier</th>
                <th className="num">Count</th>
                <th className="num">Median ₹</th>
                <th className="num">p90 ₹</th>
              </tr>
            </thead>
            <tbody>
              {ue.data.per_completed_intake.map((u, i) => (
                <tr key={i}>
                  <td>{u.channel}</td>
                  <td>{u.tier}</td>
                  <td className="num">{u.count}</td>
                  <td className="num">₹{u.median_inr}</td>
                  <td className="num">₹{u.p90_inr}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* anomalies */}
      <section>
        <h2>Anomalies</h2>
        {anomalies.data && anomalies.data.length > 0 ? (
          anomalies.data.map((a, i) => (
            <p key={i} className="error">
              <span className="pill bad">{a.kind}</span> {a.detail}
            </p>
          ))
        ) : (
          <p className="muted">Nothing unusual today.</p>
        )}
      </section>

      <WhatIf token={token} onError={onError} />
      <TierMixPanel token={token} onError={onError} />
    </>
  );
}

function Select({
  label,
  value,
  opts,
  onChange,
}: {
  label: string;
  value: string;
  opts: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label>
      <span className="muted" style={{ marginRight: 6 }}>
        {label}
      </span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {opts.map((o) => (
          <option key={o} value={o}>
            {o || "all"}
          </option>
        ))}
      </select>
    </label>
  );
}

function UeCard({ title, u }: { title: string; u?: api.UnitCost }) {
  return (
    <div className="card">
      <div className="label">{title}</div>
      <div className="value">{u?.median_inr ? `₹${u.median_inr}` : "—"}</div>
      <div className="sub">{u ? `${u.count} in window` : ""}</div>
    </div>
  );
}

function WhatIf({ token, onError }: TabProps) {
  const [provider, setProvider] = useState("");
  const [factor, setFactor] = useState("0.5");
  const [result, setResult] = useState<api.WhatIf | null>(null);

  return (
    <section>
      <h2>What-if: edit the price book</h2>
      <p className="muted">
        Recompute the last 7 days if one provider’s rate changed. Factor 0 removes it,
        0.5 halves it, 1.5 raises it 50%.
      </p>
      <div className="row">
        <input
          placeholder="provider (blank = all)"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
        />
        <input
          placeholder="factor"
          value={factor}
          onChange={(e) => setFactor(e.target.value)}
          style={{ width: 90 }}
        />
        <button
          className="action"
          onClick={async () => {
            try {
              setResult(
                await api.runWhatIf(token, [{ provider: provider || undefined, factor }]),
              );
            } catch (e) {
              onError(e);
            }
          }}
        >
          Recompute
        </button>
      </div>
      {result && (
        <p>
          Baseline <b>₹{result.baseline_inr}</b> → adjusted <b>₹{result.adjusted_inr}</b>{" "}
          (<b style={{ color: Number(result.delta_inr) <= 0 ? "var(--primary)" : "var(--danger)" }}>
            {Number(result.delta_inr) <= 0 ? "" : "+"}
            ₹{result.delta_inr}
          </b>
          )
        </p>
      )}
    </section>
  );
}

/** The other half of doc 03 §11's what-if: not "what if this rate changed" but
 *  "what if this channel had run a different tier". Both numbers are medians
 *  this hospital actually booked, so when there is nothing measured to compare
 *  against the panel says that instead of showing a modelled saving — the
 *  distinction matters, because a number here is what an operator would switch a
 *  channel's tier on. */
function TierMixPanel({ token, onError }: TabProps) {
  const [channel, setChannel] = useState("phone");
  const [from, setFrom] = useState("conversational");
  const [to, setTo] = useState("rule_based");
  const [result, setResult] = useState<api.TierMix | null>(null);

  const saved = result && Number(result.delta_inr) <= 0;

  return (
    <section>
      <h2>What-if: a different tier mix</h2>
      <p className="muted">
        Re-prices the last 7 days of completed intakes on one channel at another tier&rsquo;s
        observed median cost. Measured, not modelled — if that tier has never run on that
        channel there is no answer to give.
      </p>
      <div className="row">
        <select value={channel} onChange={(e) => setChannel(e.target.value)}>
          {["kiosk", "phone", "whatsapp", "app"].map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select value={from} onChange={(e) => setFrom(e.target.value)}>
          {MIXABLE_TIERS.map((t) => (
            <option key={t.id} value={t.id}>
              from {t.label}
            </option>
          ))}
        </select>
        <select value={to} onChange={(e) => setTo(e.target.value)}>
          {MIXABLE_TIERS.map((t) => (
            <option key={t.id} value={t.id}>
              to {t.label}
            </option>
          ))}
        </select>
        <button
          className="action"
          onClick={async () => {
            try {
              setResult(await api.runTierMix(token, channel, from, to));
            } catch (e) {
              onError(e);
            }
          }}
        >
          Recompute
        </button>
      </div>
      {result &&
        (result.basis === "observed" ? (
          <p>
            {result.intakes} completed {result.channel} intakes at ₹{result.from_median_inr}{" "}
            median → ₹{result.to_median_inr} median:{" "}
            <b>₹{result.baseline_inr}</b> becomes <b>₹{result.adjusted_inr}</b> (
            <b style={{ color: saved ? "var(--primary)" : "var(--danger)" }}>
              {saved ? "" : "+"}₹{result.delta_inr}
            </b>
            )
          </p>
        ) : (
          <p className="muted">No answer: {result.basis}.</p>
        ))}
    </section>
  );
}

// The three tiers a completed intake can have run on, with the names an operator
// uses. `TIERS` above is the filter list (blank = all, plus `paper`, which is a
// downtime record rather than a tier anything could be re-priced onto).
const MIXABLE_TIERS = [
  { id: "conversational", label: "V1 conversational" },
  { id: "rule_based", label: "V2 pipeline" },
  { id: "prerecorded", label: "V3 pre-recorded" },
];
