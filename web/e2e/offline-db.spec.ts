// The offline store's load-bearing invariants (S7), as pure-logic tests.
//
// These are not about the walker (conformance.spec.ts owns that) — they are
// about the two places the offline kiosk can silently hand two patients one
// token or lose an intake:
//
//   * the block cursor must never rewind to the server's view, and
//   * a taken token must never be handed out twice, even under concurrent draws.
//
// Runs in the conformance project (no browser, no server); IndexedDB is provided
// by fake-indexeddb.

// Installs a global indexedDB before Dexie is imported (Dexie binds the
// reference at import time, so the order matters).
import "fake-indexeddb/auto";

import { expect, test } from "@playwright/test";
import { IDBFactory } from "fake-indexeddb";

import {
  KioskDb,
  _setDb,
  blockFor,
  enqueue,
  markSynced,
  pending,
  pendingCount,
  saveBlocks,
  takeToken,
  type QueuedIntake,
} from "../app/(kiosk)/kiosk/_lib/offline/db";

function freshDb(): KioskDb {
  // A brand-new IndexedDB per test — fake-indexeddb's factory is swappable.
  globalThis.indexedDB = new IDBFactory();
  const db = new KioskDb(`test-${Math.random().toString(36).slice(2)}`);
  _setDb(db);
  return db;
}

const BLOCK = {
  departmentKey: "MEDONC",
  departmentName: "Medical Oncology",
  date: "2026-07-18",
  startNo: 500,
  endNo: 549,
  nextFree: 500,
};

function queued(clientId: string, tokenNo: number): QueuedIntake {
  return {
    clientId,
    departmentKey: "MEDONC",
    departmentName: "Medical Oncology",
    treeKey: "med_onc_new_patient",
    lang: "hi",
    tokenNo,
    answers: {},
    redFlags: [],
    chiefComplaint: null,
    caregiver: false,
    completedAt: new Date().toISOString(),
    status: "pending",
    attempts: 0,
    lastError: null,
  };
}

test.afterEach(() => _setDb(null));

test.describe("offline block cursor", () => {
  test("takeToken hands out each number exactly once, then runs dry", async () => {
    freshDb();
    await saveBlocks([{ ...BLOCK, startNo: 500, endNo: 502 }]);

    const drawn = [
      await takeToken("MEDONC"),
      await takeToken("MEDONC"),
      await takeToken("MEDONC"),
      await takeToken("MEDONC"), // block spent
    ];

    expect(drawn).toEqual([500, 501, 502, null]);
  });

  test("concurrent draws never return the same number", async () => {
    // The kiosk is single-threaded, but a double-tap can fire two draws before
    // the first commits. Dexie's rw transaction must serialise them.
    freshDb();
    await saveBlocks([{ ...BLOCK, startNo: 500, endNo: 509 }]);

    const drawn = await Promise.all(Array.from({ length: 10 }, () => takeToken("MEDONC")));
    const numbers = drawn.filter((n): n is number => n !== null);

    expect(new Set(numbers).size).toBe(numbers.length); // no duplicates
    expect(numbers.sort((a, b) => a - b)).toEqual([500, 501, 502, 503, 504, 505, 506, 507, 508, 509]);
  });

  test("re-leasing never rewinds the cursor behind what was issued offline", async () => {
    // The regression that hands out a number twice: the kiosk issued up to 505
    // offline; the server, which never saw those, re-leases with next_free=500.
    // Taking the server's word would re-issue 500-505.
    freshDb();
    await saveBlocks([BLOCK]);
    await takeToken("MEDONC"); // 500
    await takeToken("MEDONC"); // 501
    await takeToken("MEDONC"); // 502

    // Server re-leases the same range, still at its stale view.
    await saveBlocks([{ ...BLOCK, nextFree: 500 }]);

    const block = await blockFor("MEDONC");
    expect(block?.nextFree).toBe(503); // not rewound to 500
    expect(await takeToken("MEDONC")).toBe(503);
  });

  test("a genuinely new block for a new day replaces the old cursor", async () => {
    freshDb();
    await saveBlocks([BLOCK]);
    await takeToken("MEDONC"); // 500

    // A different range (new day, new start) is not the same block — take it as
    // given rather than carrying yesterday's cursor forward.
    await saveBlocks([{ ...BLOCK, date: "2026-07-19", startNo: 600, endNo: 649, nextFree: 600 }]);

    const block = await blockFor("MEDONC");
    expect(block?.startNo).toBe(600);
    expect(block?.nextFree).toBe(600);
  });
});

test.describe("offline queue", () => {
  test("pending lists only unsynced intakes, oldest first", async () => {
    freshDb();
    await enqueue({ ...queued("c-1", 500), completedAt: "2026-07-18T09:00:00Z" });
    await enqueue({ ...queued("c-2", 501), completedAt: "2026-07-18T09:05:00Z" });
    await enqueue({ ...queued("c-3", 502), completedAt: "2026-07-18T09:02:00Z" });
    await markSynced("c-2");

    const rows = await pending();
    expect(rows.map((r) => r.clientId)).toEqual(["c-1", "c-3"]);
    expect(await pendingCount()).toBe(2);
  });

  test("marking synced removes an intake from the pending set", async () => {
    freshDb();
    await enqueue(queued("c-1", 500));
    expect(await pendingCount()).toBe(1);
    await markSynced("c-1");
    expect(await pendingCount()).toBe(0);
  });
});
