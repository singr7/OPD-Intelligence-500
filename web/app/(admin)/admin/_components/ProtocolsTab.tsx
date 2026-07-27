"use client";

// Check-in protocol templates (doc 03 §9/§10, S18-late) — the panel that reads
// the live bank and the rail that publishes a new one.
//
// Its single job: show what a signed note commits a patient to — which days she
// is asked something, and what escalates — and let someone change it and put it
// live. In order: the families and their rungs, the grading rules that can ring
// a phone, and the version rail.
//
// Why the editing surface is the document and not a form per protocol: the
// backend validator's real guarantees are properties of the *whole* bank (no
// orphaned question set, no tied precedence, no rung naming a set that does not
// exist), so the bank is versioned and validated as one document. Every save
// goes through `protocols.parse` and comes back with the validator's own
// sentence when it is refused — which is the useful part, and the part a
// field-by-field form would hide.

import { useState } from "react";
import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

export function ProtocolsTab({ token, onError }: TabProps) {
  const bank = useLoad(() => api.fetchProtocolTemplates(token), onError);
  const versions = useLoad(() => api.fetchProtocolBanks(token), onError);
  const [editing, setEditing] = useState(false);

  function reload() {
    bank.reload();
    versions.reload();
  }

  return (
    <>
      <section>
        <h2>Check-in protocol bank</h2>
        <p className="muted">
          Live: version {bank.data?.version ?? "…"} from{" "}
          <code>{bank.data?.source ?? "seeds/protocols.json"}</code>. A signed dictation picks
          the family with the highest precedence it matches; the days and question sets below
          are copied into the plan unchanged, and the personalisation may only rewrite the
          covering message. A check-in already sent keeps the questions and the rules it was
          created with — publishing changes the next plan, never a patient mid-follow-up.
        </p>
        <table>
          <thead>
            <tr>
              <th>Family</th>
              <th>Matches</th>
              <th>Cycle</th>
              <th>Asks</th>
            </tr>
          </thead>
          <tbody>
            {(bank.data?.protocols ?? []).map((p) => (
              <tr key={p.key}>
                <td>
                  <b>{p.label}</b>
                  <div className="muted">
                    {p.key} · precedence {p.precedence}
                  </div>
                </td>
                <td className="muted">
                  {[...p.matches.drug_classes, ...p.matches.keywords].slice(0, 4).join(", ")}
                </td>
                <td>{p.cycle_days > 0 ? `${p.cycle_days} days` : "—"}</td>
                <td>
                  {p.checkins.map((c) => (
                    <div key={c.day_offset}>
                      <b>D+{c.day_offset}</b> {c.asks_about}{" "}
                      <span className="muted">
                        ({c.questions} questions, {c.grading_rules} rules)
                      </span>
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>What escalates</h2>
        <p className="muted">
          Deterministic and authored — no model decides a check-in grade, for the same reason
          none decides a red flag. Green is the absence of a fired rule.
        </p>
        {(bank.data?.question_sets ?? []).map((set) => (
          <div key={set.key} className="set-card">
            <b>{set.title}</b> <span className="muted">({set.key})</span>
            <ul>
              {set.grading.map((rule) => (
                <li key={rule.id}>
                  <span className={`pill ${rule.grade === "red" ? "bad" : "approaching"}`}>
                    {rule.grade}
                  </span>{" "}
                  {rule.reason}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section>
        <h2>Versions</h2>
        <p className="muted">
          Exactly one version is live. Publishing an older version is how you roll back.
        </p>
        {versions.error && <p className="error">{versions.error}</p>}
        <table>
          <thead>
            <tr>
              <th className="num">Version</th>
              <th>Status</th>
              <th className="num">Families</th>
              <th className="num">Question sets</th>
              <th>Note</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(versions.data ?? []).map((v) => (
              <tr key={v.id}>
                <td className="num">v{v.version}</td>
                <td>
                  <span className={`pill ${v.status}`}>{v.status}</span>
                </td>
                <td className="num">{v.protocol_count}</td>
                <td className="num">{v.question_set_count}</td>
                <td className="muted">{v.notes ?? "—"}</td>
                <td className="num">
                  {v.status !== "published" && (
                    <PublishButton
                      token={token}
                      version={v.version}
                      onDone={reload}
                      onError={onError}
                    />
                  )}
                </td>
              </tr>
            ))}
            {versions.data?.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No stored versions — run <code>make seed</code> to load the authored bank.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="ghost" onClick={() => setEditing((e) => !e)}>
            {editing ? "Close editor" : "Edit the bank"}
          </button>
        </div>
        {editing && <BankEditor token={token} onSaved={reload} onError={onError} />}
      </section>

      <section>
        <h2>Slot templates</h2>
        <div className="notice">
          Moved to the <b>People &amp; roster</b> tab (S-GL.2), where the weekly grid sits beside
          the doctors it belongs to. This panel was a placeholder from S18E until then.
        </div>
      </section>

      <section>
        <h2>Downtime drill</h2>
        <div className="notice">
          Run the downtime drill from the <b>Coordinator</b> console — it owns the queue’s
          downtime state (one switch, one audit trail). Admin does not keep a second copy.
        </div>
      </section>
    </>
  );
}

function PublishButton({
  token,
  version,
  onDone,
  onError,
}: {
  token: string;
  version: number;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      className="action"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await api.publishProtocolBank(token, version);
          onDone();
        } catch (e) {
          onError(e);
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "…" : "Publish"}
    </button>
  );
}

/** The document editor. Loads the newest stored bank, saves an edit as a new
 *  draft, and shows the validator's own refusal when it will not take it —
 *  which is the whole safety story, so it is shown verbatim rather than
 *  summarised. */
function BankEditor({
  token,
  onSaved,
  onError,
}: {
  token: string;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const doc = useLoad(() => api.fetchProtocolBankDocument<unknown>(token), onError);
  const [text, setText] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const value = text ?? (doc.data ? JSON.stringify(doc.data, null, 2) : "");

  async function save() {
    setBusy(true);
    setRefusal(null);
    setSaved(null);
    try {
      const parsed = JSON.parse(value);
      const version = await api.saveProtocolBankDraft(token, parsed, notes || undefined);
      setSaved(version.version);
      onSaved();
    } catch (e) {
      // A JSON syntax error and the server's clinical refusal read the same way
      // to the person editing: this is why it will not take it.
      setRefusal(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bank-editor">
      <p className="muted">
        Saving creates a new <b>draft</b>. It is validated as a whole — a question set no
        protocol asks, two families sharing a precedence, a rule over a free-text answer or a
        rule that grades green are all refused here rather than discovered on a patient.
      </p>
      <label className="field">
        <span>What changed, and why (a clinical reviewer reads this)</span>
        <input value={notes} onChange={(e) => setNotes(e.target.value)} style={{ width: "100%" }} />
      </label>
      <textarea
        className="doc"
        rows={22}
        spellCheck={false}
        value={value}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row">
        <button className="action" onClick={save} disabled={busy || !value}>
          {busy ? "Validating…" : "Save as new draft"}
        </button>
        {saved !== null && (
          <span className="muted">
            Saved as v{saved}. Publish it from the table above when it has been reviewed.
          </span>
        )}
      </div>
      {refusal && <p className="error">Refused: {refusal}</p>}
    </div>
  );
}
