"use client";

// The price-book editor (doc 03 §10). Lists the book newest-effective first and
// adds a new versioned row — a rate change is never an in-place edit (history
// re-prices at the rate that was in force). Saving invalidates the server's price
// cache so the new rate takes effect immediately, and self-audits.

import { useState } from "react";
import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

const UNITS = ["token_in", "token_out", "audio_sec", "call_min", "msg", "char"];
const today = () => new Date().toISOString().slice(0, 10);

export function PriceBookTab({ token, onError }: TabProps) {
  const book = useLoad(() => api.fetchPriceBook(token), onError);
  const [form, setForm] = useState({
    provider: "",
    model: "*",
    unit: "token_in",
    price_inr: "",
    effective_from: today(),
    notes: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  async function add() {
    setBusy(true);
    setError(null);
    try {
      await api.addPriceRow(token, { ...form, notes: form.notes || undefined });
      setForm((f) => ({ ...f, price_inr: "", notes: "" }));
      book.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section>
        <h2>Add a rate</h2>
        <p className="muted">
          Model <code>*</code> is a flat per-provider rate (SMS, telephony, WhatsApp). A later
          date supersedes an existing rate rather than overwriting it.
        </p>
        <div className="row">
          <input placeholder="provider" value={form.provider} onChange={(e) => set("provider", e.target.value)} />
          <input placeholder="model" value={form.model} onChange={(e) => set("model", e.target.value)} style={{ width: 120 }} />
          <select value={form.unit} onChange={(e) => set("unit", e.target.value)}>
            {UNITS.map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
          <input placeholder="₹ price" value={form.price_inr} onChange={(e) => set("price_inr", e.target.value)} style={{ width: 100 }} />
          <input type="date" value={form.effective_from} onChange={(e) => set("effective_from", e.target.value)} />
          <button className="action" onClick={add} disabled={busy || !form.provider || !form.price_inr}>
            {busy ? "Saving…" : "Add row"}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      <section>
        <h2>Price book</h2>
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th>Unit</th>
              <th className="num">₹ / unit</th>
              <th>Effective</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {book.data?.map((r) => (
              <tr key={r.id}>
                <td>{r.provider}</td>
                <td>{r.model}</td>
                <td>{r.unit}</td>
                <td className="num">₹{r.price_inr}</td>
                <td>{r.effective_from}</td>
                <td className="muted">{r.notes ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
