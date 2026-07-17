// Red-flag rule evaluation — a line-by-line port of backend/app/trees/rules.py.
//
// ## Why this file exists, and why it is dangerous
//
// The kiosk must keep working with the server unreachable (doc 01 §5: "the
// hospital ran on paper yesterday; downtime mode is paper with a memory"). An
// offline intake still has to know that a fever after chemo is an emergency, so
// the rule evaluator has to run here too.
//
// That makes this a SECOND implementation of the one thing STATE.md says no
// model and no vendor may ever decide. Two implementations of clinical logic can
// drift, and a drift here is silent: a flag that fires in Python and not in
// TypeScript is a patient who is urgent on the server and routine on the kiosk.
//
// The mitigation is not care. It is `web/e2e/conformance.spec.ts`, which replays
// golden traces recorded from the Python evaluator (over every seeded tree)
// through this code and fails the build on any divergence. If you change
// rules.py, regenerate with `make tree-fixtures` — CI checks the fixtures are in
// sync with rules.py, so a change here without one there cannot merge.
//
// ## What is deliberately NOT ported
//
// `validate()`. Validation is a publish-time act on the server (S18/S21), and a
// tree only reaches this file after `parse()` accepted it. Porting the validator
// would double the drift surface for no gain — the kiosk never authors a rule.
//
// Evaluation is total, exactly like Python's: an unknown op or a type mismatch
// is `false`, never a throw. A patient mid-intake is not the person to punish
// for a bad rule, and `validate` has already made that unreachable.

import type { Rule } from "./types";

const GROUP_OPS = new Set(["and", "or", "not"]);
const NUMERIC_OPS = new Set(["gt", "gte", "lt", "lte"]);

export type AnswerValues = Record<string, unknown>;

export function isGroup(rule: Rule): boolean {
  return "rules" in rule;
}

/** Is this rule true of `values` (node id → answered value)? */
export function evaluate(rule: Rule, values: AnswerValues): boolean {
  if (!isPlainObject(rule)) return false;

  if (isGroup(rule)) {
    const op = rule["op"];
    const sub = Array.isArray(rule["rules"]) ? (rule["rules"] as Rule[]) : [];
    // Python's all()/any() over an empty list are True/False respectively;
    // Array.every/some match that, so an empty group behaves identically.
    if (op === "and") return sub.every((r) => evaluate(r, values));
    if (op === "or") return sub.some((r) => evaluate(r, values));
    if (op === "not") return !sub.some((r) => evaluate(r, values));
    return false;
  }
  return evaluateLeaf(rule, values);
}

function evaluateLeaf(rule: Rule, values: AnswerValues): boolean {
  const op = rule["op"];
  const nodeId = rule["node"];
  if (typeof nodeId !== "string" || typeof op !== "string") return false;

  const present =
    Object.prototype.hasOwnProperty.call(values, nodeId) &&
    values[nodeId] !== null &&
    values[nodeId] !== undefined;

  if (op === "answered") return present;
  if (op === "unanswered") return !present;
  // Silence is not evidence. Every content op is false without an answer.
  if (!present) return false;

  const answer = values[nodeId];
  const expected = rule["value"];

  if (NUMERIC_OPS.has(op)) {
    const left = asNumber(answer);
    const right = asNumber(expected);
    if (left === null || right === null) return false;
    if (op === "gt") return left > right;
    if (op === "gte") return left >= right;
    if (op === "lt") return left < right;
    return left <= right;
  }

  if (op === "eq") return scalarEq(answer, expected);
  if (op === "ne") return !scalarEq(answer, expected);
  if (op === "in") {
    if (!Array.isArray(expected)) return false;
    return expected.some((item) => scalarEq(answer, item));
  }
  if (op === "contains") {
    if (!Array.isArray(answer)) return false;
    return answer.some((item) => scalarEq(item, expected));
  }
  return false;
}

/** Numbers only. A bool is not 1 — mirrors `_as_number`, where a bool sneaking
 *  into a temperature comparison is an authoring bug, not a 1°C fever.
 *  (`typeof true === "boolean"`, so JS excludes it without Python's special
 *  case; NaN/Infinity cannot survive JSON.) */
function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Mirrors `_scalar_eq`. */
function scalarEq(left: unknown, right: unknown): boolean {
  if (typeof left === "boolean" || typeof right === "boolean") {
    // Python: `left is right` — True is not 1.
    return left === right;
  }
  if (typeof left === "number" && typeof right === "number") {
    return left === right;
  }
  if (typeof left === "string" || typeof right === "string") {
    return left === right;
  }
  // Python's `==` compares lists/dicts structurally where JS `===` compares
  // identity. No validated rule reaches here (eq/ne/in address single|scale|
  // number, and `contains` compares option ids), but matching Python exactly is
  // cheaper than reasoning about why it cannot.
  return deepEq(left, right);
}

function deepEq(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (left === null || right === null) return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  if (typeof left !== "object" || typeof right !== "object") return false;
  return JSON.stringify(left) === JSON.stringify(right);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Every node id a rule reads (`referenced_nodes`). */
export function referencedNodes(rule: Rule): Set<string> {
  if (!isPlainObject(rule)) return new Set();
  if (isGroup(rule)) {
    const found = new Set<string>();
    const sub = Array.isArray(rule["rules"]) ? (rule["rules"] as Rule[]) : [];
    for (const child of sub) {
      for (const id of referencedNodes(child)) found.add(id);
    }
    return found;
  }
  const nodeId = rule["node"];
  return typeof nodeId === "string" ? new Set([nodeId]) : new Set();
}

export { GROUP_OPS, NUMERIC_OPS };
