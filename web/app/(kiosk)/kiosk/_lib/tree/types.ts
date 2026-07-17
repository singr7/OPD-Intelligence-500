// The canonical tree shape — `Tree.to_json()` in backend/app/trees/schema.py.
//
// This is NOT the authored form in seeds/trees/*.json. The server ships trees
// already parsed, validated and desugared (option-level `flag: true` and
// node-level `red_flag` have become real entries in `red_flags`), so nothing on
// this side re-implements the validator or the sugar. A tree that reaches the
// kiosk is one `parse()` already approved.
//
// Keep these types in lockstep with the Python dataclasses. The conformance
// suite (web/e2e/conformance.spec.ts) is what actually enforces that: it replays
// golden traces recorded from the Python walker through the TS one.

export type Localized = Record<string, string>;

export type NodeType =
  | "single"
  | "multi"
  | "scale"
  | "number"
  | "body_map"
  | "free_voice";

/** Node kinds whose answer is a list of option ids — they branch by `default`
 *  only, because a list cannot select one edge. Mirrors `_LIST_ANSWER_TYPES`. */
export const LIST_ANSWER_TYPES: ReadonlySet<NodeType> = new Set<NodeType>([
  "multi",
  "body_map",
]);

export type TreeOption = {
  id: string;
  text: Localized;
  icon: string | null;
};

export type TreeNode = {
  id: string;
  type: NodeType;
  text: Localized;
  audio: Localized;
  options: TreeOption[];
  /** option id (single only) or "default" → next node id, or null to end. */
  next: Record<string, string | null>;
  min: number | null;
  max: number | null;
  unit: string | null;
  adaptive_hints: string | null;
};

export type Severity = "routine" | "semi" | "urgent";

/** A rule expression: a group `{op, rules}` or a leaf `{node, op, value?}`.
 *  Untyped on purpose — it is evaluated structurally, exactly as Python does. */
export type Rule = Record<string, unknown>;

export type RedFlagSpec = {
  id: string;
  severity: Severity;
  when: Rule;
  label: Localized;
  instruction: Localized;
  source_node: string | null;
};

export type Tree = {
  key: string;
  version: number;
  department: string | null;
  title: Localized;
  languages: string[];
  root: string;
  nodes: TreeNode[];
  red_flags: RedFlagSpec[];
};

/** `key@vN` — what gets logged onto an intake (`Tree.ref`). */
export function treeRef(tree: Tree): string {
  return `${tree.key}@v${tree.version}`;
}

export function nodeIndex(tree: Tree): Map<string, TreeNode> {
  return new Map(tree.nodes.map((node) => [node.id, node]));
}

/** The question in the patient's language, falling back to English rather than
 *  raising — same stance as `Node.ask`. */
export function ask(node: TreeNode, lang: string): string {
  return node.text[lang] || node.text["en"] || "";
}

export function audioClip(node: TreeNode, lang: string): string | null {
  return node.audio[lang] ?? null;
}

export function option(node: TreeNode, optionId: string): TreeOption | null {
  return node.options.find((o) => o.id === optionId) ?? null;
}
