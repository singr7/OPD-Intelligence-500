// Reconnect: hand the outage's intakes back (S7, doc 01 §5).
//
// > "Everything syncs automatically on reconnect; conflicts resolve by
// > timestamp, tokens never collide because blocks are pre-allocated." — doc 01 §5
//
// ## A rejection is not a retry
//
// The three outcomes are different in kind, and collapsing them loses patients:
//
//   synced     the server has it. Stop.
//   duplicate  an earlier attempt landed before the network dropped again. This
//              is a *success* — the usual one, in fact, since the network
//              typically returns mid-batch. Stop.
//   rejected   the server refused it and will refuse it again (an unknown tree,
//              a token outside the block). Retrying forever would hide it; a
//              human has to look. Park it and surface the count.
//
// Anything else — a 500, a dropped connection — is neither, and stays pending so
// the next reconnect tries again.
//
// ## Sync never blocks an intake
//
// It runs in the background and its failures are invisible to the patient at the
// kiosk. The queue is durable, the tokens are already partitioned, and the next
// attempt costs nothing. The one thing sync must never do is make a patient wait.

import { kioskApi } from "../api";
import { kioskId, markAttempted, markRejected, markSynced, pending } from "./db";

export type SyncSummary = {
  attempted: number;
  synced: number;
  duplicates: number;
  rejected: number;
  failed: number;
};

const EMPTY: SyncSummary = { attempted: 0, synced: 0, duplicates: 0, rejected: 0, failed: 0 };

let running = false;

/** Push every pending intake. Safe to call often — concurrent calls collapse, so
 *  a reconnect event and the heartbeat firing together do not double-send. */
export async function syncPending(): Promise<SyncSummary> {
  if (running) return EMPTY;
  running = true;
  try {
    const rows = await pending();
    if (rows.length === 0) return EMPTY;

    const id = await kioskId();
    const result = await kioskApi.sync({
      kiosk_id: id,
      intakes: rows.map((row) => ({
        client_id: row.clientId,
        department_key: row.departmentKey,
        tree_key: row.treeKey,
        lang: row.lang,
        token_no: row.tokenNo,
        answers: row.answers,
        chief_complaint: row.chiefComplaint,
        caregiver: row.caregiver,
        completed_at: row.completedAt,
      })),
    });

    const summary: SyncSummary = { ...EMPTY, attempted: rows.length };
    for (const outcome of result.results) {
      if (outcome.status === "synced") {
        await markSynced(outcome.client_id);
        summary.synced += 1;
      } else if (outcome.status === "duplicate") {
        // Already landed. A success, not a problem.
        await markSynced(outcome.client_id);
        summary.duplicates += 1;
      } else {
        await markRejected(outcome.client_id, outcome.error ?? "rejected");
        summary.rejected += 1;
      }
    }
    return summary;
  } catch (error) {
    // The network went away again mid-batch. Everything unanswered stays
    // pending; note the attempt so a stuck kiosk is visible to staff.
    const rows = await pending();
    const message = error instanceof Error ? error.message : String(error);
    for (const row of rows) await markAttempted(row.clientId, message);
    return { ...EMPTY, attempted: rows.length, failed: rows.length };
  } finally {
    running = false;
  }
}
