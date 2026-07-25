"use client";

// Message-template registry + voice-pack coverage (doc 03 §10), both read-only.
//
// Templates are code-defined (a Meta submission has to match the repo), so the
// console shows completeness across the four languages rather than editing them —
// a DB-backed editable registry is S18-late/S15. Voice packs show what the V3
// trees expect: every clip is `recorded: false` today (TTS fallback) until S7/S21
// records the human voice, so this is a coverage checklist, not an uploader yet.

import { useMemo } from "react";
import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

const LANGS = ["en", "hi", "mr", "te"];

export function RegistryTab({ token, onError }: TabProps) {
  const templates = useLoad(() => api.fetchTemplates(token), onError);
  const packs = useLoad(() => api.fetchVoicePacks(token), onError);

  // Group templates by name → which languages exist, to show the 4-language grid.
  const byName = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const t of templates.data ?? []) {
      if (!m.has(t.name)) m.set(t.name, new Set());
      m.get(t.name)!.add(t.lang);
    }
    return m;
  }, [templates.data]);

  const recorded = packs.data?.filter((p) => p.recorded).length ?? 0;
  const total = packs.data?.length ?? 0;

  return (
    <>
      <section>
        <h2>WhatsApp templates</h2>
        <p className="muted">
          Read-only — code-defined so a Meta submission matches the repo. Every template must
          carry all four languages or the first out-of-window send fails.
        </p>
        <table>
          <thead>
            <tr>
              <th>Template</th>
              {LANGS.map((l) => (
                <th key={l}>{l}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[...byName.entries()].map(([name, langs]) => (
              <tr key={name}>
                <td>{name}</td>
                {LANGS.map((l) => (
                  <td key={l}>
                    {langs.has(l) ? (
                      <span className="pill ok">✓</span>
                    ) : (
                      <span className="pill bad">missing</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Voice packs (V3)</h2>
        <p className="muted">
          {recorded} of {total} clips recorded — the rest fall through to TTS. Real human
          recordings arrive with S7/S21; this is the coverage checklist.
        </p>
        <table>
          <thead>
            <tr>
              <th>Tree</th>
              <th>Node</th>
              <th>Lang</th>
              <th>Clip</th>
              <th>Recorded</th>
            </tr>
          </thead>
          <tbody>
            {packs.data?.slice(0, 60).map((p, i) => (
              <tr key={i}>
                <td>{p.tree_key}</td>
                <td>{p.node_id}</td>
                <td>{p.lang}</td>
                <td className="muted">{p.clip_name ?? "—"}</td>
                <td>
                  {p.recorded ? (
                    <span className="pill ok">recorded</span>
                  ) : (
                    <span className="pill draft">TTS</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {total > 60 && <p className="muted">Showing first 60 of {total}.</p>}
      </section>
    </>
  );
}
