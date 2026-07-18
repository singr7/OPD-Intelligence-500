// The kiosk's local memory (S7, doc 01 §5).
//
// > "Local-first kiosk & queue board. Kiosk and queue-board are PWAs with
// > IndexedDB. If the server is unreachable, the kiosk keeps issuing tokens from
// > a pre-allocated offline token block and stores intakes locally." — doc 01 §5
//
// Four stores, each with one job:
//
//   bundle   the trees + department chooser (GET /kiosk/bundle), so a walk can
//            run with nothing but this tab
//   blocks   the leased token ranges (POST /kiosk/blocks/lease) and how far into
//            them this kiosk has gone
//   queue    finished offline intakes waiting to sync
//   meta     the kiosk's identity and small flags
//
// ## The block cursor is the one piece of truly local truth
//
// `nextFree` lives here and is authoritative while offline — the server cannot
// see it, and the numbers it produces are already on paper slips in patients'
// hands by the time anyone syncs. So it is only ever advanced, never reset from
// the server: `lease` on the server returns `used_up_to` as the server last
// heard it, which is *behind* reality during an outage. Taking the server's word
// after a reboot mid-outage would re-issue numbers already handed out.

import Dexie, { type Table } from "dexie";

import type { Tree } from "../tree/types";
import type { AnswersJson } from "../tree/walker";

export type BundleRow = {
  id: "current";
  etag: string;
  fetchedAt: string;
  departments: { key: string; name: string }[];
  trees: Tree[];
};

export type BlockRow = {
  /** department key — one block per department (offline there is no classifier,
   *  so the patient may pick any of them). */
  departmentKey: string;
  departmentName: string;
  date: string;
  startNo: number;
  endNo: number;
  /** The next number this kiosk will hand out. Local truth (see above). */
  nextFree: number;
};

export type QueuedIntake = {
  /** The kiosk's id for this intake; the server's idempotency key. */
  clientId: string;
  departmentKey: string;
  departmentName: string;
  treeKey: string;
  lang: string;
  tokenNo: number;
  answers: AnswersJson;
  redFlags: { id: string; severity: string }[];
  chiefComplaint: string | null;
  caregiver: boolean;
  completedAt: string;
  /** "pending" → not yet accepted; "synced" → the server has it; "rejected" →
   *  the server refused it and retrying will not help (a human must look). */
  status: "pending" | "synced" | "rejected";
  attempts: number;
  lastError: string | null;
};

export type MetaRow = { key: string; value: string };

export class KioskDb extends Dexie {
  bundle!: Table<BundleRow, string>;
  blocks!: Table<BlockRow, string>;
  queue!: Table<QueuedIntake, string>;
  meta!: Table<MetaRow, string>;

  constructor(name = "opd-kiosk") {
    super(name);
    this.version(1).stores({
      bundle: "id",
      blocks: "departmentKey, date",
      queue: "clientId, status, completedAt",
      meta: "key",
    });
  }
}

let db: KioskDb | null = null;

/** The one database handle. Lazy, because this module is imported by code that
 *  also runs during SSR and in the conformance suite, where indexedDB is absent. */
export function getDb(): KioskDb {
  if (db === null) db = new KioskDb();
  return db;
}

/** Testing seam: point at a throwaway database. */
export function _setDb(next: KioskDb | null): void {
  db = next;
}

export function hasIndexedDb(): boolean {
  return typeof indexedDB !== "undefined";
}

// -- meta ---------------------------------------------------------------------

const KIOSK_ID_KEY = "kioskId";

/** This terminal's stable id. It scopes the leased token blocks, so it must
 *  survive a reload — a kiosk that forgets its id leases a *second* set of
 *  blocks and starts handing out numbers from a range it does not own. */
export async function kioskId(): Promise<string> {
  const store = getDb();
  const found = await store.meta.get(KIOSK_ID_KEY);
  if (found) return found.value;
  const generated = `kiosk-${crypto.randomUUID().slice(0, 8)}`;
  await store.meta.put({ key: KIOSK_ID_KEY, value: generated });
  return generated;
}

export async function setKioskId(value: string): Promise<void> {
  await getDb().meta.put({ key: KIOSK_ID_KEY, value });
}

// -- bundle -------------------------------------------------------------------

export async function saveBundle(row: Omit<BundleRow, "id">): Promise<void> {
  await getDb().bundle.put({ ...row, id: "current" });
}

export async function loadBundle(): Promise<BundleRow | undefined> {
  return getDb().bundle.get("current");
}

export async function treeFor(departmentKey: string): Promise<Tree | null> {
  const cached = await loadBundle();
  if (!cached) return null;
  const candidates = cached.trees.filter((tree) => tree.department === departmentKey);
  if (candidates.length === 0) return null;
  // Same preference as the server's `app.kiosk.select_tree`: a walk-in with no
  // history gets the new-patient intake, else the routing tree.
  candidates.sort((a, b) => (a.key < b.key ? -1 : 1));
  return (
    candidates.find((tree) => tree.key.endsWith("_new_patient")) ??
    candidates.find((tree) => tree.key.endsWith("_routing")) ??
    candidates[0]
  );
}

// -- blocks -------------------------------------------------------------------

export async function saveBlocks(rows: BlockRow[]): Promise<void> {
  const store = getDb();
  await store.transaction("rw", store.blocks, async () => {
    for (const row of rows) {
      const existing = await store.blocks.get(row.departmentKey);
      // Never rewind the cursor to the server's view: during an outage the
      // server's `used_up_to` is behind what this kiosk has already handed out,
      // and re-issuing those numbers is two patients holding one token.
      const nextFree =
        existing && existing.startNo === row.startNo
          ? Math.max(existing.nextFree, row.nextFree)
          : row.nextFree;
      await store.blocks.put({ ...row, nextFree });
    }
  });
}

export async function blockFor(departmentKey: string): Promise<BlockRow | undefined> {
  return getDb().blocks.get(departmentKey);
}

/** Take the next offline token for a department, advancing the local cursor.
 *  Returns null when the block is spent — the caller must fall back to paper
 *  (doc 01 §5 step 3) rather than invent a number. */
export async function takeToken(departmentKey: string): Promise<number | null> {
  const store = getDb();
  return store.transaction("rw", store.blocks, async () => {
    const block = await store.blocks.get(departmentKey);
    if (!block) return null;
    if (block.nextFree > block.endNo) return null;
    const token = block.nextFree;
    await store.blocks.put({ ...block, nextFree: token + 1 });
    return token;
  });
}

// -- queue --------------------------------------------------------------------

export async function enqueue(intake: QueuedIntake): Promise<void> {
  await getDb().queue.put(intake);
}

export async function pending(): Promise<QueuedIntake[]> {
  const rows = await getDb().queue.where("status").equals("pending").toArray();
  // Oldest first: the queue is a record of what happened, and it syncs in the
  // order it happened.
  rows.sort((a, b) => (a.completedAt < b.completedAt ? -1 : 1));
  return rows;
}

export async function pendingCount(): Promise<number> {
  return getDb().queue.where("status").equals("pending").count();
}

export async function markSynced(clientId: string): Promise<void> {
  await getDb().queue.update(clientId, { status: "synced", lastError: null });
}

export async function markRejected(clientId: string, error: string): Promise<void> {
  await getDb().queue.update(clientId, { status: "rejected", lastError: error });
}

export async function markAttempted(clientId: string, error: string): Promise<void> {
  const row = await getDb().queue.get(clientId);
  if (!row) return;
  await getDb().queue.update(clientId, { attempts: row.attempts + 1, lastError: error });
}
