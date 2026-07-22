// One intake vocabulary, two backings (S7).
//
// `KioskApp` calls start → answer → finish → confirm and does not want to know
// whether the API is up. This controller is that seam: each method tries the
// server, and on a network failure — or when the monitor already knows we are in
// downtime — runs the intake locally against the ported walker, returning the
// *same shapes* the server does so the screens render either without a branch.
//
// The decision is per call, not per session, with one exception: once a session
// has started locally it stays local (its walk lives in this tab, not on the
// server), and once it started on the server it stays on the server. Mixing the
// two mid-intake would mean rebuilding a half-finished walk across the boundary,
// which is exactly the failover the tier ladder handles server-side and has no
// analogue here — the patient is standing at one kiosk for one intake.
//
// See ./local.ts for what an offline intake can and cannot do (no classifier, no
// model summary — the same V3 tier the kiosk always runs, minus the two model
// calls).

import type { AnswerResult, ConfirmResult, FinishResult, StartResult } from "../api";
import { ApiError, kioskApi } from "../api";
import type { NetMonitor } from "./net";
import {
  canRunOffline,
  confirmLocal,
  currentWireNode,
  getLocalSession,
  isLocalSession,
  readback,
  startLocal,
} from "./local";
import type { LocalSession } from "./local";

/** A network-level failure (offline), as opposed to a 4xx the server returned on
 *  purpose. Only the former falls back to local. */
function isOffline(error: unknown): boolean {
  // ApiError means the server answered — that is not an outage, and a 422 must
  // not be papered over by pretending we are offline.
  if (error instanceof ApiError) return false;
  return true;
}

export type FlowDeps = { net: NetMonitor };

export type StartInput = {
  lang: string;
  chiefComplaint: string;
  caregiver: boolean;
  deptKey?: string;
  deptName?: string;
};

export function makeFlow({ net }: FlowDeps) {
  /** Start online if we can, else run locally. A local start needs a chosen
   *  department (offline there is no classifier), so the caller passes one from
   *  the chooser. */
  async function start(input: StartInput): Promise<StartResult> {
    const goLocal = net.current.downtime && input.deptKey !== undefined;
    if (!goLocal) {
      try {
        const res = await kioskApi.start({
          lang: input.lang,
          chief_complaint: input.chiefComplaint || "—",
          caregiver: input.caregiver,
          dept_key: input.deptKey,
        });
        net.observedSuccess();
        return res;
      } catch (error) {
        if (!isOffline(error)) throw error;
        net.observedFailure();
        // fall through to local
      }
    }

    if (input.deptKey === undefined || input.deptName === undefined) {
      // No department and no server to classify: the kiosk must show the chooser
      // from the cached bundle. `KioskApp` handles that before calling start.
      throw new OfflineNeedsDepartment();
    }
    if (!(await canRunOffline(input.deptKey))) {
      throw new OfflineUnavailableForDept(input.deptKey);
    }

    const session = await startLocal({
      lang: input.lang,
      chiefComplaint: input.chiefComplaint,
      caregiver: input.caregiver,
      departmentKey: input.deptKey,
      departmentName: input.deptName,
    });
    return localStartResult(session);
  }

  async function answer(
    sessionId: string,
    // `attempt` is the adaptive-intake voice retry counter (doc 11 §5); it only
    // matters on the online path (a local walk never interprets), forwarded to the
    // server and ignored by localAnswer.
    input: { node_id: string; value: unknown; raw_text?: string | null; attempt?: number }
  ): Promise<AnswerResult> {
    if (isLocalSession(sessionId)) return localAnswer(sessionId, input);
    // A server session cannot be advanced offline — its walk is on the server.
    // If this call fails the intake is lost, which is the pre-existing online
    // behaviour; the offline-first guarantee is for intakes that *started*
    // offline. (In the demo the outage precedes the intake, so it starts local.)
    const res = await kioskApi.answer(sessionId, input);
    net.observedSuccess();
    return res;
  }

  async function finish(sessionId: string): Promise<FinishResult> {
    if (isLocalSession(sessionId)) return localFinish(sessionId);
    const res = await kioskApi.finish(sessionId);
    net.observedSuccess();
    return res;
  }

  async function confirm(sessionId: string): Promise<ConfirmResult> {
    if (isLocalSession(sessionId)) return localConfirm(sessionId);
    const res = await kioskApi.confirm(sessionId);
    net.observedSuccess();
    return res;
  }

  return { start, answer, finish, confirm };
}

export type Flow = ReturnType<typeof makeFlow>;

export class OfflineNeedsDepartment extends Error {}
export class OfflineUnavailableForDept extends Error {
  constructor(public readonly deptKey: string) {
    super(`this kiosk cannot run ${deptKey} offline (no cached tree or no token block)`);
  }
}

// -- shaping local results like the server's ----------------------------------

function localStartResult(session: LocalSession): StartResult {
  const node = currentWireNode(session);
  return {
    status: "routed",
    session_id: session.sessionId,
    lang: session.lang,
    tier: "prerecorded",
    department: { key: session.departmentKey, name: session.departmentName },
    tree_key: session.tree.key,
    node,
    complete: node === null,
  };
}

function requireLocal(sessionId: string): LocalSession {
  const session = getLocalSession(sessionId);
  if (!session) {
    // The tab reloaded mid-intake and the in-memory walk is gone. The patient is
    // still standing there; the kiosk restarts them (reset()), same as any lost
    // session.
    throw new Error(`no live local session ${sessionId}`);
  }
  return session;
}

function localAnswer(
  sessionId: string,
  input: { node_id: string; value: unknown; raw_text?: string | null; attempt?: number }
): AnswerResult {
  const session = requireLocal(sessionId);
  try {
    session.walk.save(input.node_id, input.value, {
      text: input.raw_text ?? null,
      lang: session.lang,
    });
  } catch {
    // The walker refused the answer (bad option, out of range) — the same
    // re-ask the server returns as ok:false, not a crash.
    return {
      ok: false,
      node_id: input.node_id,
      complete: false,
      error: "invalid answer",
      red_flags: [],
      node: currentWireNode(session),
    };
  }

  const node = currentWireNode(session);
  return {
    ok: true,
    node_id: input.node_id,
    complete: node === null,
    error: null,
    red_flags: session.walk.redFlags().map((h) => ({ id: h.id, severity: h.severity })),
    node,
  };
}

function localFinish(sessionId: string): FinishResult {
  const session = requireLocal(sessionId);
  return {
    readback: readback(session),
    summary_md: null, // the server writes the real summary at sync
    red_flags: session.walk.redFlags().map((h) => ({ id: h.id, severity: h.severity })),
    complete: session.walk.isComplete,
  };
}

async function localConfirm(sessionId: string): Promise<ConfirmResult> {
  const session = requireLocal(sessionId);
  const result = await confirmLocal(session);
  return {
    token_no: result.tokenNo,
    department: { key: result.departmentKey, name: result.departmentName },
    red_flags: result.redFlags.map((h) => ({ id: h.id, severity: h.severity })),
    cost_inr: "0.0000", // a pure-V3 offline intake costs nothing per turn
  };
}
