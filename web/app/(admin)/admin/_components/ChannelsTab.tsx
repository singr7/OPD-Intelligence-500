"use client";

// The switchboard (S-GL.1, doc 12 §1) — which channels are open, and what each
// vendor needs before one can be.
//
// The screen is built around one distinction the backend keeps and this console
// must not collapse: a channel is open only if it is **switched on** *and* the
// vendor it needs is **ready**, and those are different problems with different
// fixes. "Switched off" is somebody's decision; "no Meta credentials" is a job.
// A single green dot would hide which of the two you are looking at, so every row
// shows both, and the switch is deliberately not rendered as authoritative — a
// channel switched on with no credentials still reads Closed, because that is
// what a patient would find.
//
// Credentials are write-only. Nothing here can display one: the API does not
// return them, the form's fields start empty, and a field left blank means "leave
// this alone" rather than "clear it". The only things shown back are whether a
// vendor is complete, which fields are still missing, and what the vendor itself
// said the last time it was tested.

import { useState } from "react";
import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

const CHANNEL_LABEL: Record<string, string> = {
  kiosk: "Kiosk",
  phone: "Phone",
  whatsapp: "WhatsApp",
  app: "Android app",
};

const VENDOR_LABEL: Record<string, string> = {
  "messaging:meta": "WhatsApp — Meta Cloud API",
  "telephony:exotel": "Phone — Exotel",
};

export function ChannelsTab({ token, onError }: TabProps) {
  const channels = useLoad(() => api.fetchChannels(token), onError);
  const versions = useLoad(() => api.fetchChannelVersions(token), onError);
  const credentials = useLoad(() => api.fetchProviderCredentials(token), onError);
  const [editing, setEditing] = useState(false);

  function reload() {
    channels.reload();
    versions.reload();
    credentials.reload();
  }

  const open = (channels.data?.channels ?? []).filter((c) => c.open);

  return (
    <>
      <section>
        <h2>Channels</h2>
        <p className="muted">
          A channel is open only if it is switched on <b>and</b> the vendor it needs is configured.
          The switch is a decision; readiness is a fact — it is computed from what is actually
          configured and cannot be asserted from this screen. So a hospital that forgets to close
          WhatsApp before opening its doors still has a closed WhatsApp.
        </p>
        {channels.data && (
          <div className="notice">
            {open.length === 0 ? (
              <b>Nothing is open. No patient can register on any channel.</b>
            ) : (
              <>
                <b>
                  Open now: {open.map((c) => CHANNEL_LABEL[c.channel] ?? c.channel).join(", ")}.
                </b>{" "}
                {channels.data.from_file
                  ? "Running from config/tiers.yaml — nothing has been published from this console yet."
                  : `Running published version v${channels.data.version}.`}
              </>
            )}
          </div>
        )}
        {channels.error && <p className="error">{channels.error}</p>}
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th>Status</th>
              <th>Switch</th>
              <th>Vendor</th>
              <th>Tier ladder</th>
              <th className="num">GPU seats</th>
            </tr>
          </thead>
          <tbody>
            {(channels.data?.channels ?? []).map((c) => (
              <tr key={c.channel}>
                <td>
                  <b>{CHANNEL_LABEL[c.channel] ?? c.channel}</b>
                </td>
                <td>
                  <span className={`pill ${c.open ? "published" : "bad"}`}>
                    {c.open ? "Open" : "Closed"}
                  </span>
                  {!c.open && c.reason && <div className="muted">{c.reason}</div>}
                </td>
                <td className="muted">{c.enabled ? "on" : "off"}</td>
                <td className="muted">
                  {/* A caveat replaces "configured" rather than sitting under it:
                      "configured / no real vendor is connected" reads as a
                      contradiction, and the caveat is the true half. */}
                  {c.note ? c.note : c.ready ? "configured" : "not configured"}
                </td>
                <td className="muted">{c.ladder.join(" → ")}</td>
                <td className="num">{c.max_concurrent || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">
          The ladder is the order a channel prefers its speech stack in; it falls to the next rung
          when the one above is unhealthy or full. <b>GPU seats</b> is a channel’s private share of
          the {channels.data?.max_oss_sessions ?? 12} concurrent local voice sessions the box holds
          — phone has one so that twelve calls cannot starve the patient standing at the kiosk, who
          cannot be rung back.
        </p>
        {channels.data && Object.keys(channels.data.campaign_mix).length > 0 && (
          <p className="muted">
            <b>Campaign mix:</b>{" "}
            {Object.entries(channels.data.campaign_mix)
              .map(([channel, pct]) => `${pct}% by ${channel}`)
              .join(", ")}{" "}
            — how tomorrow’s D-1 list is invited. Fixed per patient, so re-planning never reshuffles
            who gets a call.
          </p>
        )}
      </section>

      <section>
        <h2>Vendor credentials</h2>
        <p className="muted">
          Entered here, encrypted, and live within a few seconds — no restart and no deploy. They
          are never shown back: leave a field blank to keep what is stored. <code>.env</code> is the
          floor, so removing a credential returns the box to whatever was deployed with it.
        </p>
        {credentials.data?.some((c) => c.derived_key) && (
          <div className="notice">
            No <code>SECRETS_KEY</code> is set, so credentials are encrypted with a key derived from{" "}
            <code>JWT_SECRET</code>. They work, but the two secrets are coupled: rotating the JWT
            secret makes every stored credential unreadable and they must be entered again.
          </div>
        )}
        {(credentials.data ?? []).map((credential) => (
          <CredentialCard
            key={credential.provider}
            token={token}
            credential={credential}
            onChanged={reload}
            onError={onError}
          />
        ))}
      </section>

      <section>
        <h2>Versions</h2>
        <p className="muted">
          Exactly one version is live, and publishing an older one is how you roll back. With none
          published, <code>config/tiers.yaml</code> is what runs — the file is the floor, exactly as
          the seed files are for trees and protocols.
        </p>
        {versions.error && <p className="error">{versions.error}</p>}
        <table>
          <thead>
            <tr>
              <th className="num">Version</th>
              <th>Status</th>
              <th>Open channels</th>
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
                <td className="muted">
                  {Object.entries(v.enabled)
                    .filter(([, on]) => on)
                    .map(([channel]) => CHANNEL_LABEL[channel] ?? channel)
                    .join(", ") || "none"}
                </td>
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
                <td colSpan={5} className="muted">
                  Nothing published — the box is running <code>config/tiers.yaml</code>.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="ghost" onClick={() => setEditing((e) => !e)}>
            {editing ? "Close editor" : "Edit the channel document"}
          </button>
        </div>
        {editing && <ChannelEditor token={token} onSaved={reload} onError={onError} />}
      </section>
    </>
  );
}

/** One vendor: whether it is complete, what it last said, and a write-only form. */
function CredentialCard({
  token,
  credential,
  onChanged,
  onError,
}: {
  token: string;
  credential: api.ProviderCredential;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"save" | "test" | "clear" | null>(null);
  const [result, setResult] = useState<api.ProviderTest | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const last =
    result ?? (credential.last_test.at ? (credential.last_test as api.ProviderTest) : null);

  async function run(kind: "save" | "test" | "clear") {
    setBusy(kind);
    setRefusal(null);
    try {
      if (kind === "save") {
        await api.setProviderCredentials(token, credential.provider, values);
        setValues({});
      } else if (kind === "test") {
        setResult(await api.testProvider(token, credential.provider));
      } else {
        await api.clearProviderCredentials(token, credential.provider);
        setResult(null);
      }
      onChanged();
    } catch (e) {
      setRefusal(e instanceof Error ? e.message : String(e));
      onError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="set-card">
      <b>{VENDOR_LABEL[credential.provider] ?? credential.provider}</b>{" "}
      <span className={`pill ${credential.configured ? "published" : "bad"}`}>
        {credential.configured ? "configured" : credential.unreadable ? "unreadable" : "not set"}
      </span>{" "}
      <span className="muted">
        {credential.source === "console"
          ? "set from this console"
          : credential.source === "env"
            ? "from .env"
            : "nothing stored"}
      </span>
      {credential.unreadable && (
        <p className="error">
          Stored credentials cannot be decrypted with the current key — the encryption key changed.
          Enter them again.
        </p>
      )}
      {credential.missing.length > 0 && (
        <p className="muted">Still needs: {credential.missing.join(", ")}</p>
      )}
      {last && (
        <p className={last.ok ? "muted" : "error"}>
          Last test {last.ok ? "succeeded" : "failed"}: {last.detail}
        </p>
      )}
      <div className="cred-fields">
        {credential.fields.map((field) => (
          <label key={field} className="field">
            <span>{field}</span>
            <input
              type="password"
              autoComplete="off"
              placeholder="unchanged"
              value={values[field] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
            />
          </label>
        ))}
      </div>
      <div className="row">
        <button
          className="action"
          disabled={busy !== null || Object.values(values).every((v) => !v)}
          onClick={() => run("save")}
        >
          {busy === "save" ? "Saving…" : "Save credentials"}
        </button>
        <button className="ghost" disabled={busy !== null} onClick={() => run("test")}>
          {busy === "test" ? "Testing…" : "Test connection"}
        </button>
        {credential.source === "console" && (
          <button className="ghost" disabled={busy !== null} onClick={() => run("clear")}>
            {busy === "clear" ? "Removing…" : "Remove (fall back to .env)"}
          </button>
        )}
      </div>
      {refusal && <p className="error">Refused: {refusal}</p>}
    </div>
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
          await api.publishChannels(token, version);
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

/** The document editor, in the same shape as the protocol bank's: the whole
 *  document, validated server-side, with the validator's own refusal shown
 *  verbatim. The cross-checks that matter here — a seat share larger than the
 *  box, a campaign mix that does not sum to 100 — are properties of the document
 *  rather than of one channel, which is why this is not a form per row. */
function ChannelEditor({
  token,
  onSaved,
  onError,
}: {
  token: string;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const doc = useLoad(() => api.fetchChannelDocument<unknown>(token), onError);
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
      const version = await api.saveChannelDraft(token, JSON.parse(value), notes || undefined);
      setSaved(version.version);
      onSaved();
    } catch (e) {
      setRefusal(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bank-editor">
      <p className="muted">
        Saving creates a new <b>draft</b>; publishing it is the act that opens or closes a channel,
        and it takes effect on the next intake with no deploy. A ladder naming a tier that cannot
        run, a channel reserving more GPU seats than the box has, or a campaign mix that does not
        sum to 100 are all refused here rather than discovered at nine in the morning.
      </p>
      <label className="field">
        <span>What changed, and why (the next person reads this)</span>
        <input value={notes} onChange={(e) => setNotes(e.target.value)} style={{ width: "100%" }} />
      </label>
      <textarea
        className="doc"
        rows={20}
        spellCheck={false}
        value={value}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row">
        <button className="action" onClick={save} disabled={busy || !value}>
          {busy ? "Validating…" : "Save as new draft"}
        </button>
        {saved !== null && (
          <span className="muted">Saved as v{saved}. Publish it from the table above.</span>
        )}
      </div>
      {refusal && <p className="error">Refused: {refusal}</p>}
    </div>
  );
}
