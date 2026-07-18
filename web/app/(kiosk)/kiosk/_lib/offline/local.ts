// The offline intake — the four-tool contract, served from this tab (S7).
//
// When the API is unreachable the kiosk still has to run a complete intake
// (doc 01 §5). This is that path: the same start → answer → finish → confirm
// shape `app/routes/kiosk.py` serves online, backed by the ported walker instead
// of the network, so `KioskApp` drives one vocabulary either way.
//
// ## What offline cannot do, and does not pretend to
//
// **Route Q1.** The department classifier is a model call; there is no model. So
// an offline intake goes straight to the chooser — which is not a downgrade
// invented here, it is the exact path S6 already takes when the classifier
// returns `needs_human` (doc 03 §1a: "call staff / tap fallback always visible").
// The patient's spoken complaint is still recorded verbatim and still reaches the
// doctor; only the routing guess is missing.
//
// **Summarise.** The read-back is rendered from the answers by `readback()`
// rather than written by a model, which is what tier V3 has always meant. The
// server writes the real summary when the intake syncs.
//
// **Issue a number of its own choosing.** The token comes from the leased block
// (`takeToken`), never from a counter. See app/offline.py for why that partition
// is the whole of the no-collision promise.
//
// ## The tier is not a lie
//
// A kiosk intake is V3 (`prerecorded`) online too — taps, no model in the walk —
// so an offline intake is not a degraded tier, it is the same tier with the
// classifier and the summariser absent. That is why the answers JSONB this
// produces is byte-identical to the server's, and why sync can replay it into
// ordinary rows.

import { treeRef, type NodeType, type Tree } from "../tree/types";
import { Walk } from "../tree/walker";
import type { AnswersJson, RedFlagHit } from "../tree/walker";
import { blockFor, enqueue, takeToken, treeFor } from "./db";

export type LocalSession = {
  /** Prefixed so a local id can never be mistaken for a server session id. */
  sessionId: string;
  clientId: string;
  tree: Tree;
  walk: Walk;
  lang: string;
  departmentKey: string;
  departmentName: string;
  chiefComplaint: string;
  caregiver: boolean;
  startedAt: string;
};

/** Live local sessions, by id. In memory only: an intake is a person standing at
 *  the kiosk, and if the tab reloads mid-intake they are still standing there and
 *  will start again. What must survive a reload is a *finished* intake, and that
 *  is in the Dexie queue the moment it completes. */
const sessions = new Map<string, LocalSession>();

export function isLocalSession(sessionId: string): boolean {
  return sessionId.startsWith("local-");
}

export function getLocalSession(sessionId: string): LocalSession | undefined {
  return sessions.get(sessionId);
}

export class OfflineUnavailable extends Error {}

/** Can this kiosk run an intake with no server right now? */
export async function canRunOffline(departmentKey: string): Promise<boolean> {
  const tree = await treeFor(departmentKey);
  if (tree === null) return false;
  const block = await blockFor(departmentKey);
  return block !== undefined && block.nextFree <= block.endNo;
}

export async function startLocal(input: {
  lang: string;
  chiefComplaint: string;
  caregiver: boolean;
  departmentKey: string;
  departmentName: string;
}): Promise<LocalSession> {
  const tree = await treeFor(input.departmentKey);
  if (tree === null) {
    throw new OfflineUnavailable(
      `no cached tree for ${input.departmentKey} — this kiosk has never been online`
    );
  }

  const session: LocalSession = {
    sessionId: `local-${crypto.randomUUID()}`,
    clientId: `c-${crypto.randomUUID()}`,
    tree,
    walk: new Walk(tree),
    lang: input.lang,
    departmentKey: input.departmentKey,
    departmentName: input.departmentName,
    chiefComplaint: input.chiefComplaint,
    caregiver: input.caregiver,
    startedAt: new Date().toISOString(),
  };
  sessions.set(session.sessionId, session);
  return session;
}

/** The read-back script, rendered from the answers (doc 03 §1a: "summary screen:
 *  icon-chip summary + full audio read-back + confirm").
 *
 *  Deliberately plain. The model writes the doctor's summary when this syncs;
 *  what the patient must hear now is their own answers repeated back, and a
 *  template cannot get that wrong the way a paraphrase can. */
export function readback(session: LocalSession): string {
  const lines: string[] = [];
  for (const nodeId of session.walk.path()) {
    const answer = session.walk.answers.get(nodeId);
    if (!answer) continue;
    const node = session.tree.nodes.find((n) => n.id === nodeId);
    if (!node) continue;
    const question = node.text[session.lang] || node.text["en"] || "";
    lines.push(`${question} — ${describe(node.options, answer.value, session.lang, answer.text)}`);
  }
  return lines.join("\n");
}

function describe(
  options: Tree["nodes"][number]["options"],
  value: unknown,
  lang: string,
  spoken: string | null
): string {
  const label = (id: string) => {
    const option = options.find((o) => o.id === id);
    return option ? option.text[lang] || option.text["en"] || id : id;
  };
  if (Array.isArray(value)) return value.map((v) => label(String(v))).join(", ");
  if (typeof value === "string" && options.length > 0) return label(value);
  // free_voice: the patient's own words are the answer.
  if (typeof value === "string") return spoken ?? value;
  return String(value);
}

export type LocalConfirm = {
  tokenNo: number | null;
  departmentKey: string;
  departmentName: string;
  redFlags: RedFlagHit[];
  /** True when the block is spent and the patient must be sent to the desk for a
   *  paper token (doc 01 §5 step 3). Never invent a number. */
  needsPaper: boolean;
};

/** Finish an offline intake: take a token from the block and queue it for sync.
 *
 *  The queue write and the token draw happen together and before anything is
 *  shown to the patient — the number on the screen is a promise, and an intake
 *  that showed a token but was never queued is a patient standing in a queue the
 *  hospital has no record of. */
export async function confirmLocal(session: LocalSession): Promise<LocalConfirm> {
  const redFlags = session.walk.redFlags();
  const token = await takeToken(session.departmentKey);

  if (token === null) {
    return {
      tokenNo: null,
      departmentKey: session.departmentKey,
      departmentName: session.departmentName,
      redFlags,
      needsPaper: true,
    };
  }

  await enqueue({
    clientId: session.clientId,
    departmentKey: session.departmentKey,
    departmentName: session.departmentName,
    treeKey: session.tree.key,
    lang: session.lang,
    tokenNo: token,
    answers: session.walk.toJSON() as AnswersJson,
    // Advisory only: the server recomputes flags from the answers at sync and
    // never reads this. It is here so the kiosk can show the urgent chip during
    // the outage, and so a rejected intake can be triaged by a human.
    redFlags: redFlags.map((hit) => ({ id: hit.id, severity: hit.severity })),
    chiefComplaint: session.chiefComplaint || null,
    caregiver: session.caregiver,
    completedAt: new Date().toISOString(),
    status: "pending",
    attempts: 0,
    lastError: null,
  });

  sessions.delete(session.sessionId);

  return {
    tokenNo: token,
    departmentKey: session.departmentKey,
    departmentName: session.departmentName,
    redFlags,
    needsPaper: false,
  };
}

export function localTreeRef(session: LocalSession): string {
  return treeRef(session.tree);
}

// -- rendering ----------------------------------------------------------------

/** A canonical node → the wire `KioskNode` the screens render, in the patient's
 *  language. The online `/kiosk` routes do this server-side; offline we do it
 *  here from the cached tree, so the two produce the same shape and `KioskApp`
 *  renders either without knowing which. */
export type WireNode = {
  id: string;
  type: NodeType;
  text: string;
  options: { id: string; text: string; icon: string | null }[];
  min: number | null;
  max: number | null;
  unit: string | null;
  audio: string | null;
};

export function renderNode(tree: Tree, nodeId: string, lang: string): WireNode | null {
  const node = tree.nodes.find((n) => n.id === nodeId);
  if (!node) return null;
  return {
    id: node.id,
    type: node.type,
    text: node.text[lang] || node.text["en"] || "",
    options: node.options.map((option) => ({
      id: option.id,
      text: option.text[lang] || option.text["en"] || option.id,
      icon: option.icon,
    })),
    min: node.min,
    max: node.max,
    unit: node.unit,
    // The clip name for V3 audio; the caller resolves it against the pack or
    // falls back to TTS/Web Speech. Offline that fallback is Web Speech only.
    audio: node.audio[lang] ?? null,
  };
}

/** The current node of a live local session, as a wire node (or null if done). */
export function currentWireNode(session: LocalSession): WireNode | null {
  const current = session.walk.current;
  return current ? renderNode(session.tree, current.id, session.lang) : null;
}
