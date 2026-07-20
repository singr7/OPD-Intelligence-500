"use client";

// Batch-enter one paper intake sheet after a blackout (doc 01 §5 pt 3). The
// coordinator types what a patient wrote on a laminated sheet: it creates the
// same Visit + Intake a kiosk would, keeps the token from the printed paper
// block, and enqueues it. Marking it urgent is the one place a human, not the
// rules, sets priority — so it carries a written reason.

import { useEffect, useState } from "react";
import { AuthError, paperEntry } from "@/app/_lib/queue";

export function PaperEntryTab({
  token,
  departments,
  onDone,
}: {
  token: string;
  departments: { key: string; name: string }[];
  onDone: () => void;
}) {
  const [dept, setDept] = useState("");
  const [tokenNo, setTokenNo] = useState("");
  const [lang, setLang] = useState("hi");
  const [chief, setChief] = useState("");
  const [name, setName] = useState("");
  const [urgent, setUrgent] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!dept && departments.length) setDept(departments[0].key);
  }, [departments, dept]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await paperEntry(token, {
        department_key: dept,
        token_no: Number(tokenNo),
        lang,
        chief_complaint: chief || undefined,
        patient_name: name || undefined,
        urgent,
        urgent_reason: urgent ? reason || undefined : undefined,
      });
      setMsg({ ok: true, text: `Token ${tokenNo} entered and queued.` });
      setTokenNo("");
      setChief("");
      setName("");
      setUrgent(false);
      setReason("");
      onDone();
    } catch (err) {
      if (err instanceof AuthError) return;
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Could not enter." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="paper">
      <form className="paper-form" onSubmit={submit}>
        <h2>Enter a paper intake</h2>
        <p className="paper-lead">
          From a laminated downtime sheet. Use the token the patient is already holding.
        </p>

        <div className="row">
          <label>
            Department
            <select value={dept} onChange={(e) => setDept(e.target.value)}>
              {departments.length === 0 && <option value="">No departments</option>}
              {departments.map((d) => (
                <option key={d.key} value={d.key}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Token number
            <input
              value={tokenNo}
              onChange={(e) => setTokenNo(e.target.value)}
              inputMode="numeric"
              required
            />
          </label>
          <label>
            Language
            <select value={lang} onChange={(e) => setLang(e.target.value)}>
              <option value="hi">Hindi</option>
              <option value="en">English</option>
              <option value="mr">Marathi</option>
              <option value="te">Telugu</option>
            </select>
          </label>
        </div>

        <label>
          Patient name (optional)
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>

        <label>
          Chief complaint
          <textarea value={chief} onChange={(e) => setChief(e.target.value)} rows={2} />
        </label>

        <label className="check">
          <input type="checkbox" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} />
          Mark urgent (jumps the queue)
        </label>
        {urgent && (
          <label>
            Reason (shown as the chip)
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        )}

        <button type="submit" disabled={busy || !dept || !tokenNo}>
          {busy ? "Entering…" : "Enter into queue"}
        </button>
        {msg && <p className={msg.ok ? "ok" : "bad"}>{msg.text}</p>}
      </form>
    </div>
  );
}
