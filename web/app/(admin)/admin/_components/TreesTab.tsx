"use client";

// The tree builder's list + publish surface (doc 03 §10; the S18 headline AC).
// Lists every version of every tree, shows which one is live, and publishes a
// version — which the intake path (store.resolve_tree) serves on the very next
// walk-in, no deploy. Full visual node editing is a larger build; this session
// ships the version/publish/inspect loop and the on-box "edit the JSON, publish,
// it's live" path (the AC), with red-flag rules edited inside the same JSON.

import { useState } from "react";
import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

export function TreesTab({ token, onError }: TabProps) {
  const trees = useLoad(() => api.fetchTrees(token), onError);
  const [busy, setBusy] = useState<string | null>(null);
  const [inspect, setInspect] = useState<string | null>(null);

  async function publish(key: string, version: number) {
    setBusy(`${key}@${version}`);
    try {
      await api.publishTree(token, key, version);
      trees.reload();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <h2>Question trees</h2>
      <p className="muted">
        Publishing a version makes it live on the kiosk on the next intake — no deploy.
        Exactly one version per tree is live at a time.
      </p>
      {trees.error && <p className="error">{trees.error}</p>}
      <table>
        <thead>
          <tr>
            <th>Tree</th>
            <th>Dept</th>
            <th className="num">Version</th>
            <th className="num">Nodes</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {trees.data?.map((t) => (
            <tr key={t.id}>
              <td>{t.key}</td>
              <td>{t.department_code ?? "—"}</td>
              <td className="num">v{t.version}</td>
              <td className="num">{t.node_count}</td>
              <td>
                <span className={`pill ${t.status}`}>{t.status}</span>
              </td>
              <td className="num">
                <button
                  className="ghost"
                  onClick={() => setInspect(inspect === t.key + t.version ? null : t.key + t.version)}
                >
                  view
                </button>{" "}
                {t.status !== "published" && (
                  <button
                    className="action"
                    disabled={busy === `${t.key}@${t.version}`}
                    onClick={() => publish(t.key, t.version)}
                  >
                    {busy === `${t.key}@${t.version}` ? "…" : "Publish"}
                  </button>
                )}
              </td>
            </tr>
          ))}
          {trees.data?.length === 0 && (
            <tr>
              <td colSpan={6} className="muted">
                No trees in the database yet — run the seed (`make seed`) to load the authored bank.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {inspect && <TreeJson token={token} keyVersion={inspect} trees={trees.data ?? []} onError={onError} />}
    </section>
  );
}

function TreeJson({
  token,
  keyVersion,
  trees,
  onError,
}: {
  token: string;
  keyVersion: string;
  trees: api.TreeVersion[];
  onError: (e: unknown) => void;
}) {
  // keyVersion is key+version concatenated; recover the key by longest-prefix match.
  const match = trees.find((t) => keyVersion === t.key + t.version);
  const json = useLoad(
    () =>
      match
        ? fetch(`${api.API_BASE}/admin/trees/${match.key}?version=${match.version}`, {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
          }).then((r) => r.json())
        : Promise.resolve(null),
    onError,
    [keyVersion],
  );
  return (
    <div style={{ marginTop: 12 }}>
      <pre>{json.data ? JSON.stringify(json.data, null, 2) : "loading…"}</pre>
    </div>
  );
}
