// The offline tree walker — a port of backend/app/trees/walker.py.
//
// Runs when the API is unreachable (doc 01 §5). Online, the server's walker is
// still the one that decides; this is the downtime half of the same object, and
// `conformance.spec.ts` pins the two together against golden traces recorded
// from the Python original. Read the header of ./rules.ts for why that gate is
// the only thing making a second implementation of clinical logic acceptable.
//
// Every invariant STATE.md names for the Python walker holds here, for the same
// reasons:
//
//   * **Position is derived, never stored.** No cursor. `current` re-walks from
//     the root every time. A cursor would be a second source of truth about
//     where the patient is, and the two would disagree exactly when the kiosk
//     was failing over between offline and online — the worst possible moment.
//   * **Answers off the live branch are pruned.** An amendment reroutes the
//     walk; answers gathered down the abandoned branch are answers to questions
//     this patient was never asked, and would otherwise reach the doctor.
//   * **Red flags are recomputed, never accumulated**, so an amendment that
//     removes the alarming answer removes the flag.

import { evaluate } from "./rules";
import type { AnswerValues } from "./rules";
import {
  LIST_ANSWER_TYPES,
  option,
  type RedFlagSpec,
  type Severity,
  type Tree,
  type TreeNode,
} from "./types";

const SEVERITY_ORDER: Record<Severity, number> = {
  routine: 0,
  semi: 1,
  urgent: 2,
};

/** The answer does not fit the question — re-ask, do not crash. Mirrors
 *  `AnswerError`: distinct from a broken tree, which nobody should be asked. */
export class AnswerError extends Error {}

export type Answer = {
  node_id: string;
  value: unknown;
  /** The patient's own words. The doctor reads the quote (doc 03 §4), and a
   *  mis-mapped option is only recoverable if what was said survived. */
  text: string | null;
  text_en: string | null;
  lang: string | null;
  at: string;
};

export type RedFlagHit = {
  id: string;
  severity: Severity;
  label: Record<string, string>;
  instruction: Record<string, string>;
  source_node: string | null;
};

/** The `Intake.answers` JSONB shape — identical across every tier and channel. */
export type AnswersJson = Record<
  string,
  {
    value: unknown;
    text: string | null;
    text_en: string | null;
    lang: string | null;
    at: string;
  }
>;

export class Walk {
  readonly tree: Tree;
  private readonly index: Map<string, TreeNode>;
  private answersMap: Map<string, Answer>;

  constructor(tree: Tree, answers?: Iterable<[string, Answer]>) {
    this.tree = tree;
    this.index = new Map(tree.nodes.map((node) => [node.id, node]));
    this.answersMap = new Map(answers ?? []);
    this.prune();
  }

  node(nodeId: string): TreeNode {
    const found = this.index.get(nodeId);
    if (!found) {
      throw new Error(`tree ${this.tree.key}: no node ${JSON.stringify(nodeId)}`);
    }
    return found;
  }

  // ---- position ---------------------------------------------------------

  /** The node to ask now, or null when the tree is done. */
  get current(): TreeNode | null {
    for (const node of this.traverse()) {
      if (!this.answersMap.has(node.id)) return node;
    }
    return null;
  }

  get isComplete(): boolean {
    return this.current === null;
  }

  /** Node ids on the live path, in ask order, including the current one. */
  path(): string[] {
    return this.traverse().map((node) => node.id);
  }

  /** Nodes from the root along the branch the answers select. */
  private traverse(): TreeNode[] {
    const out: TreeNode[] = [];
    const seen = new Set<string>();
    let nodeId: string | null = this.tree.root;

    while (nodeId !== null && nodeId !== undefined) {
      if (seen.has(nodeId)) {
        // parse() rejects cycles, so this is unreachable for a served tree.
        throw new Error(`cycle at ${nodeId} in validated tree ${this.tree.key}`);
      }
      seen.add(nodeId);
      const node = this.node(nodeId);
      out.push(node);
      const answer = this.answersMap.get(nodeId);
      if (answer === undefined) return out;
      nodeId = this.edge(node, answer.value);
    }
    return out;
  }

  /** Which branch an answer selects: option-keyed for `single` (the schema
   *  allows it nowhere else), `default` otherwise. */
  private edge(node: TreeNode, value: unknown): string | null {
    if (
      node.type === "single" &&
      typeof value === "string" &&
      Object.prototype.hasOwnProperty.call(node.next, value)
    ) {
      return node.next[value];
    }
    return node.next["default"] ?? null;
  }

  // ---- answers ----------------------------------------------------------

  get answers(): Map<string, Answer> {
    return new Map(this.answersMap);
  }

  /** Record an answer to `nodeId`, which must be the current question or one
   *  already answered (an amendment). Answering some other node is refused
   *  rather than accepted quietly — a tree that can be answered out of order is
   *  one whose branch conditions were never really asked. */
  save(
    nodeId: string,
    value: unknown,
    opts: {
      text?: string | null;
      text_en?: string | null;
      lang?: string | null;
      at?: string;
    } = {}
  ): Answer {
    const node = this.node(nodeId);
    const current = this.current;
    if (!this.answersMap.has(nodeId) && (current === null || current.id !== nodeId)) {
      const asked =
        current === null ? "the intake is complete" : `the question is '${current.id}'`;
      throw new AnswerError(
        `cannot answer '${nodeId}': it is not the current question (${asked}). ` +
          "Answer the current node, or amend one already answered."
      );
    }

    const answer: Answer = {
      node_id: nodeId,
      value: validateAnswer(node, value),
      text: opts.text ?? null,
      text_en: opts.text_en ?? null,
      lang: opts.lang ?? null,
      at: opts.at ?? new Date().toISOString(),
    };
    this.answersMap.set(nodeId, answer);
    this.prune();
    return answer;
  }

  /** Drop answers no longer on the path (`_prune`). */
  private prune(): void {
    const live = new Set(this.traverse().map((node) => node.id));
    for (const nodeId of [...this.answersMap.keys()]) {
      if (!live.has(nodeId)) this.answersMap.delete(nodeId);
    }
  }

  /** node id → value, the shape the rule evaluator reads. */
  values(): AnswerValues {
    const out: AnswerValues = {};
    for (const [nodeId, answer] of this.answersMap) out[nodeId] = answer.value;
    return out;
  }

  // ---- red flags --------------------------------------------------------

  /** Every flag the answers so far raise, worst first. */
  redFlags(): RedFlagHit[] {
    const values = this.values();
    const hits: RedFlagHit[] = this.tree.red_flags
      .filter((spec: RedFlagSpec) => evaluate(spec.when, values))
      .map((spec) => ({
        id: spec.id,
        severity: spec.severity,
        label: spec.label,
        instruction: spec.instruction,
        source_node: spec.source_node,
      }));

    // Mirrors `key=lambda hit: (-_SEVERITY_ORDER[hit.severity], hit.id)`.
    hits.sort((a, b) => {
      const bySeverity = SEVERITY_ORDER[b.severity] - SEVERITY_ORDER[a.severity];
      if (bySeverity !== 0) return bySeverity;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
    return hits;
  }

  /** The visit priority these answers earn (doc 03 §1: red flag → urgent). */
  priority(): Severity {
    const flags = this.redFlags();
    return flags.length > 0 ? flags[0].severity : "routine";
  }

  // ---- serialisation ----------------------------------------------------

  toJSON(): AnswersJson {
    const out: AnswersJson = {};
    for (const [nodeId, answer] of this.answersMap) {
      out[nodeId] = {
        value: answer.value,
        text: answer.text,
        text_en: answer.text_en,
        lang: answer.lang,
        at: answer.at,
      };
    }
    return out;
  }

  /** Rebuild a walk from stored answers. Unknown node ids are dropped, not
   *  fatal: a published tree can gain a version while an intake is in flight,
   *  and a patient mid-answer is not the person to punish for it. */
  static fromJSON(tree: Tree, data: AnswersJson | null | undefined): Walk {
    const known = new Set(tree.nodes.map((node) => node.id));
    const answers: [string, Answer][] = [];
    for (const [nodeId, raw] of Object.entries(data ?? {})) {
      if (!known.has(nodeId) || raw === null || typeof raw !== "object") continue;
      answers.push([
        nodeId,
        {
          node_id: nodeId,
          value: raw.value,
          text: raw.text ?? null,
          text_en: raw.text_en ?? null,
          lang: raw.lang ?? null,
          at: typeof raw.at === "string" ? raw.at : new Date().toISOString(),
        },
      ]);
    }
    return new Walk(tree, answers);
  }
}

/** Check a value against its node's type, returning the normalised value.
 *  Mirrors `validate_answer`. Normalisation is small but load-bearing: the JSONB
 *  an intake writes must not depend on whether the answer came from a kiosk tap
 *  or a model's function call — or, now, from offline or online. */
export function validateAnswer(node: TreeNode, value: unknown): unknown {
  if (value === null || value === undefined) {
    throw new AnswerError(`node '${node.id}': an answer is required`);
  }

  if (node.type === "single") {
    if (typeof value !== "string") {
      throw new AnswerError(`node '${node.id}': expected one option id`);
    }
    if (option(node, value) === null) {
      throw new AnswerError(
        `node '${node.id}': '${value}' is not an option ` +
          `(have: ${node.options.map((o) => o.id).join(", ")})`
      );
    }
    return value;
  }

  if (LIST_ANSWER_TYPES.has(node.type)) {
    if (!Array.isArray(value)) {
      throw new AnswerError(`node '${node.id}': expected a list of option ids`);
    }
    const chosen: string[] = [];
    for (const item of value) {
      if (typeof item !== "string" || option(node, item) === null) {
        throw new AnswerError(
          `node '${node.id}': '${String(item)}' is not an option ` +
            `(have: ${node.options.map((o) => o.id).join(", ")})`
        );
      }
      if (!chosen.includes(item)) chosen.push(item);
    }
    return chosen;
  }

  if (node.type === "scale" || node.type === "number") {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new AnswerError(`node '${node.id}': expected a number, got ${String(value)}`);
    }
    if (node.min !== null && value < node.min) {
      throw new AnswerError(`node '${node.id}': ${value} is below the minimum ${node.min}`);
    }
    if (node.max !== null && value > node.max) {
      throw new AnswerError(`node '${node.id}': ${value} is above the maximum ${node.max}`);
    }
    // Python returns int(n) if n.is_integer() else n; JS has one number type, so
    // 2.0 and 2 are already the same value and serialise identically.
    return value;
  }

  // free_voice
  if (typeof value !== "string" || value.trim() === "") {
    throw new AnswerError(`node '${node.id}': expected the patient's words`);
  }
  return value.trim();
}
