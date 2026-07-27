"use client";

// The visual tree editor (doc 03 §10, S18-late) — the S18 headline AC made real
// for someone who does not read JSON.
//
// Its single job: let a clinic person change *what a patient is asked*, see what
// that does, and put it live. In order, the three things on the screen are the
// question spine (each node, its options, the branch each option takes), the
// red-flag stamp on the nodes that can escalate, and the publish rail.
//
// **It edits words, not shape.** Text, option labels, red-flag wording and
// severity — the things that change often and want no engineer. It deliberately
// cannot add, delete or rewire a node: branching is what the validator's
// unreachable-node and cycle checks exist for, and a drag-to-rewire builder that
// silently orphans a question is a worse tool than a pull request. Structural
// edits stay in `seeds/trees/*.json`; the JSON view below is honest about that.
//
// Every save is a **new draft version** (the server assigns it) and publishing is
// a separate tap, so nothing an author types is live until they say so.

import { useMemo, useState } from "react";
import * as api from "../_lib/api";
import { useLoad } from "../_lib/useLoad";

const LANGS = ["en", "hi", "mr", "te"] as const;
type Lang = (typeof LANGS)[number];

type Txt = Record<string, string>;
type Option = { id: string; text: Txt; icon?: string; flag?: boolean };
type RedFlag = { id: string; severity: string; label: Txt; instruction: Txt };
type Node = {
  id: string;
  type: string;
  text: Txt;
  options?: Option[];
  next?: { default?: string; by_option?: Record<string, string> };
  red_flag?: RedFlag;
  red_flag_if?: unknown;
  unit?: string;
  min?: number;
  max?: number;
  summary_role?: "primary_symptom" | "duration" | "severity" | "symptom_detail" | "context" | null;
};
type Tree = {
  key: string;
  version: number;
  department?: string;
  title: Txt;
  root: string;
  nodes: Node[];
};

export function TreeEditor({
  token,
  treeKey,
  version,
  onError,
  onPublished,
  onClose,
}: {
  token: string;
  treeKey: string;
  version: number;
  onError: (e: unknown) => void;
  onPublished: () => void;
  onClose: () => void;
}) {
  const loaded = useLoad<Tree>(() => api.fetchTree(token, treeKey, version), onError, [
    treeKey,
    version,
  ]);
  const [draft, setDraft] = useState<Tree | null>(null);
  const [lang, setLang] = useState<Lang>("en");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);

  const tree = draft ?? loaded.data;
  const dirty = draft !== null;

  // Ask order, from the root, following each option's branch. The same walk the
  // patient takes, so the page reads top-to-bottom the way the intake does.
  const ordered = useMemo(() => (tree ? orderNodes(tree) : []), [tree]);

  function edit(fn: (t: Tree) => void) {
    if (!tree) return;
    const copy: Tree = JSON.parse(JSON.stringify(tree));
    fn(copy);
    setDraft(copy);
    setNote(null);
  }

  async function saveDraft() {
    if (!tree) return;
    setBusy("save");
    try {
      const saved = await api.saveTreeDraft(token, treeKey, tree);
      setSavedVersion(saved.version);
      setDraft(null);
      setNote(`Saved as draft v${saved.version}. Nothing is live until you publish it.`);
      onPublished(); // refresh the version list
    } catch (e) {
      onError(e);
      setNote(e instanceof Error ? e.message : "Could not save");
    } finally {
      setBusy(null);
    }
  }

  async function publish(v: number) {
    setBusy("publish");
    try {
      await api.publishTree(token, treeKey, v);
      setNote(`v${v} is live. The next patient to start this intake sees it.`);
      setSavedVersion(null);
      onPublished();
    } catch (e) {
      onError(e);
      setNote(e instanceof Error ? e.message : "Could not publish");
    } finally {
      setBusy(null);
    }
  }

  if (loaded.error) return <p className="error">{loaded.error}</p>;
  if (!tree) return <p className="muted">Loading {treeKey}…</p>;

  return (
    <div className="editor">
      <div className="editor-bar">
        <button className="ghost" onClick={onClose}>
          ← All trees
        </button>
        <strong>{tree.title[lang] || tree.title.en}</strong>
        <span className="muted">
          {treeKey} · v{version}
        </span>
        <span className="spacer" />
        <div className="langs" role="tablist" aria-label="Language">
          {LANGS.map((l) => (
            <button
              key={l}
              role="tab"
              aria-selected={l === lang}
              className={l === lang ? "on" : ""}
              onClick={() => setLang(l)}
            >
              {l}
            </button>
          ))}
        </div>
        <button className="action" disabled={!dirty || busy !== null} onClick={saveDraft}>
          {busy === "save" ? "Saving…" : dirty ? "Save as new draft" : "Saved"}
        </button>
        {savedVersion !== null && (
          <button
            className="action publish"
            disabled={busy !== null}
            onClick={() => publish(savedVersion)}
          >
            {busy === "publish" ? "Publishing…" : `Publish v${savedVersion}`}
          </button>
        )}
      </div>

      {note && <p className="notice editor-note">{note}</p>}
      {dirty && (
        <p className="muted editor-note">
          Unsaved. Saving creates a new draft version — it never changes a version a
          patient may already have been asked.
        </p>
      )}

      <ol className="spine">
        {ordered.map(({ node, depth, via }) => (
          <li key={node.id} className={node.red_flag ? "station flagged" : "station"} data-node={node.id}>
            <span className="rail" style={{ marginLeft: depth * 18 }} />
            <div className="station-body" style={{ marginLeft: depth * 18 }}>
              <header>
                <code>{node.id}</code>
                <span className="kind">{node.type}</span>
                {via && <span className="via">after “{via}”</span>}
                {node.red_flag && (
                  <span className={`stamp ${node.red_flag.severity}`}>
                    {node.red_flag.severity}
                  </span>
                )}
              </header>

              <label className="field">
                <span>Question</span>
                <textarea
                  rows={2}
                  value={node.text[lang] ?? ""}
                  onChange={(e) =>
                    edit((t) => {
                      nodeIn(t, node.id).text[lang] = e.target.value;
                    })
                  }
                />
              </label>

              <label className="field">
                <span>Live summary placement</span>
                <select
                  value={node.summary_role ?? ""}
                  onChange={(e) =>
                    edit((t) => {
                      const value = e.target.value;
                      nodeIn(t, node.id).summary_role =
                        value === "" ? null : (value as NonNullable<Node["summary_role"]>);
                    })
                  }
                >
                  <option value="">Not shown in live summary</option>
                  <option value="primary_symptom">Main concern</option>
                  <option value="duration">Duration</option>
                  <option value="severity">Severity</option>
                  <option value="symptom_detail">Symptom detail</option>
                  <option value="context">Context</option>
                </select>
              </label>

              {node.options && node.options.length > 0 && (
                <div className="options">
                  {node.options.map((o) => (
                    <label className="opt" key={o.id}>
                      <span className="opt-id">{o.id}</span>
                      <input
                        value={o.text[lang] ?? ""}
                        onChange={(e) =>
                          edit((t) => {
                            const opt = nodeIn(t, node.id).options?.find((x) => x.id === o.id);
                            if (opt) opt.text[lang] = e.target.value;
                          })
                        }
                      />
                      <span className="goes">
                        →{" "}
                        {node.next?.by_option?.[o.id] ??
                          node.next?.default ??
                          "end of intake"}
                      </span>
                    </label>
                  ))}
                </div>
              )}

              {node.red_flag && (
                <fieldset className="flag">
                  <legend>Red flag — what a nurse is told, and how loudly</legend>
                  <div className="flag-row">
                    {/* The two severities a red flag may carry (`Priority`, minus
                        `routine`, which the tree validator refuses as "not a red
                        flag"). Offering a value the schema does not have would
                        rewrite `semi` to `urgent` on the next save — which is the
                        difference between a patient keeping her place in the queue
                        and jumping it. */}
                    <select
                      value={node.red_flag.severity}
                      onChange={(e) =>
                        edit((t) => {
                          const rf = nodeIn(t, node.id).red_flag;
                          if (rf) rf.severity = e.target.value;
                        })
                      }
                    >
                      <option value="urgent">urgent — jumps the queue</option>
                      <option value="semi">semi — flagged, keeps its place</option>
                    </select>
                    <input
                      aria-label="Red flag label"
                      value={node.red_flag.label[lang] ?? ""}
                      onChange={(e) =>
                        edit((t) => {
                          const rf = nodeIn(t, node.id).red_flag;
                          if (rf) rf.label[lang] = e.target.value;
                        })
                      }
                    />
                  </div>
                  <textarea
                    aria-label="Red flag instruction"
                    rows={2}
                    value={node.red_flag.instruction[lang] ?? ""}
                    onChange={(e) =>
                      edit((t) => {
                        const rf = nodeIn(t, node.id).red_flag;
                        if (rf) rf.instruction[lang] = e.target.value;
                      })
                    }
                  />
                </fieldset>
              )}
            </div>
          </li>
        ))}
      </ol>

      <TestRun token={token} tree={tree} onError={onError} />

      <section>
        <button className="ghost" onClick={() => setShowJson((s) => !s)}>
          {showJson ? "Hide" : "Show"} the JSON
        </button>
        <p className="muted" style={{ marginTop: 8 }}>
          Adding, deleting or rewiring a question is a structural change — it happens in
          <code> seeds/trees/{treeKey}.json</code> and a pull request, where the validator&rsquo;s
          unreachable-question and cycle checks are reviewed by a person. This editor changes
          wording and severity.
        </p>
        {showJson && <pre>{JSON.stringify(tree, null, 2)}</pre>}
      </section>
    </div>
  );
}

// -- test run -----------------------------------------------------------------

function TestRun({
  token,
  tree,
  onError,
}: {
  token: string;
  tree: Tree;
  onError: (e: unknown) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [result, setResult] = useState<api.TestRunResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      setResult(await api.testRunTree(token, tree, coerce(answers)));
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="testrun">
      <h3>Try it</h3>
      <p className="muted">
        Answer as a patient would and see the path the questions take and the red flags it
        raises. Nothing is saved.
      </p>
      <div className="try-grid">
        {tree.nodes.map((n) => (
          <label key={n.id} className="try">
            <span>{n.text.en || n.id}</span>
            {n.options && n.options.length > 0 ? (
              <select
                value={answers[n.id] ?? ""}
                onChange={(e) => setAnswers({ ...answers, [n.id]: e.target.value })}
              >
                <option value="">—</option>
                {n.options.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.text.en || o.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={answers[n.id] ?? ""}
                placeholder={n.type}
                onChange={(e) => setAnswers({ ...answers, [n.id]: e.target.value })}
              />
            )}
          </label>
        ))}
      </div>
      <button className="action" onClick={run} disabled={busy}>
        {busy ? "Walking…" : "Run"}
      </button>
      {result && (
        <div className="try-out">
          {result.error && <p className="error">{result.error}</p>}
          <p>
            <strong>Path:</strong> {result.path.join(" → ") || "—"}{" "}
            {result.complete ? "(reaches the end)" : "(stops here)"}
          </p>
          {result.red_flags.length > 0 ? (
            <ul className="flags">
              {result.red_flags.map((f) => (
                <li key={f.id}>
                  <span className={`stamp ${f.severity}`}>{f.severity}</span> {f.label}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No red flags on this path.</p>
          )}
        </div>
      )}
    </section>
  );
}

// -- helpers ------------------------------------------------------------------

function nodeIn(tree: Tree, id: string): Node {
  const found = tree.nodes.find((n) => n.id === id);
  if (!found) throw new Error(`node ${id} vanished`);
  return found;
}

/** Numbers stay numbers: the walker's bounds check is typed, and "38" as a string
 *  would be refused by a node that accepts 38. */
function coerce(answers: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(answers)) {
    if (v === "") continue;
    out[k] = /^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v;
  }
  return out;
}

/** Depth-first from the root, so the list reads in ask order and a branch sits
 *  under the option that leads to it. Nodes no branch reaches are appended (the
 *  validator refuses those, but a draft in hand may be mid-edit). */
function orderNodes(tree: Tree): { node: Node; depth: number; via: string | null }[] {
  const byId = new Map(tree.nodes.map((n) => [n.id, n]));
  const seen = new Set<string>();
  const out: { node: Node; depth: number; via: string | null }[] = [];

  const walk = (id: string, depth: number, via: string | null) => {
    const node = byId.get(id);
    if (!node || seen.has(id)) return;
    seen.add(id);
    out.push({ node, depth, via });
    const branches = node.next?.by_option ?? {};
    for (const [optionId, target] of Object.entries(branches)) {
      const label = node.options?.find((o) => o.id === optionId)?.text.en ?? optionId;
      walk(target, depth + 1, label);
    }
    if (node.next?.default) walk(node.next.default, depth, null);
  };

  walk(tree.root, 0, null);
  for (const node of tree.nodes) if (!seen.has(node.id)) out.push({ node, depth: 0, via: null });
  return out;
}
