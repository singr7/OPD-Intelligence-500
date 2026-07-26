"use client";

// The tree panel (doc 03 §10; the S18 headline AC). Two screens: the version list
// — which tree is live, which drafts are waiting — and the visual editor behind
// "Edit". Publishing a version makes the intake path (`store.resolve_tree`) serve
// it on the very next walk-in, with no deploy.

import { useState } from "react";
import type { TabProps } from "./Console";
import { TreeEditor } from "./TreeEditor";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

export function TreesTab({ token, onError }: TabProps) {
  const trees = useLoad(() => api.fetchTrees(token), onError);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ key: string; version: number } | null>(null);

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

  if (editing) {
    return (
      <TreeEditor
        token={token}
        treeKey={editing.key}
        version={editing.version}
        onError={onError}
        onPublished={trees.reload}
        onClose={() => setEditing(null)}
      />
    );
  }

  return (
    <section>
      <h2>Question trees</h2>
      <p className="muted">
        Publishing a version makes it live on the kiosk on the next intake — no deploy.
        Exactly one version per tree is live at a time; publishing an older version is how
        you roll back.
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
                  onClick={() => setEditing({ key: t.key, version: t.version })}
                >
                  Edit
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
    </section>
  );
}
