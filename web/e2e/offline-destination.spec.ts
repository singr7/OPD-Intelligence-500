// The destination, during an outage (doc 24 §4/§5) — pure-logic, no browser.
//
// doc 24 §8 says of the offline path: "trees are content, so offline should be
// free — prove it, don't assume it". Mostly it is. What is *not* free is the
// token: an offline number comes out of a per-department leased block, so a
// patient who asks for the ayurveda OPD while the API is unreachable has to be
// given AYUR's number, not General Medicine's. Getting that wrong would not fail
// anywhere — it would sync cleanly and put her on the wrong board.
//
// The decision itself is `Walk.destination()`, which conformance.spec.ts already
// pins against the Python walker. This file is about what `confirmLocal` does
// with it.

import "fake-indexeddb/auto";

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";
import { IDBFactory } from "fake-indexeddb";

import { KioskDb, _setDb, pending, saveBlocks, saveBundle } from "../app/(kiosk)/kiosk/_lib/offline/db";
import { confirmLocal, startLocal } from "../app/(kiosk)/kiosk/_lib/offline/local";
import type { Tree } from "../app/(kiosk)/kiosk/_lib/tree/types";

/** A tree in the shape the bundle ships it — the **canonical** form, taken from
 *  the walker conformance fixture rather than from `seeds/trees/*.json`.
 *
 *  That matters here: the authored file carries `flag: true` on an option and
 *  `parse()` desugars it into a real entry in `red_flags`. Reading the authored
 *  form would give this suite a General Medicine tree with no red flags at all,
 *  and the test that a red flag cancels the ayurveda preference would pass
 *  vacuously — which is the one test in this file worth having. */
function canonicalTree(key: string): Tree {
  const fixture = JSON.parse(
    readFileSync(join(__dirname, "fixtures", "walk-conformance.json"), "utf8")
  ) as { cases: { ref: string; tree: Tree }[] };
  const found = fixture.cases.find((testCase) => testCase.ref.startsWith(`${key}@`));
  if (!found) throw new Error(`no ${key} in the conformance fixture`);
  return found.tree;
}

const DEPARTMENTS = [
  { key: "GENMED", name: "General Medicine", care_system: "allopathy" as const },
  { key: "AYUR", name: "Ayurveda", care_system: "ayurveda" as const },
];

async function cachedKiosk(): Promise<void> {
  globalThis.indexedDB = new IDBFactory();
  _setDb(new KioskDb(`test-${Math.random().toString(36).slice(2)}`));
  await saveBundle({
    etag: "e1",
    fetchedAt: new Date().toISOString(),
    hospital: { name: "Alwar District Cancer Centre", city: "Alwar" },
    departments: DEPARTMENTS,
    trees: [canonicalTree("general_medicine_routing")],
  });
  await saveBlocks([
    {
      departmentKey: "GENMED",
      departmentName: "General Medicine",
      date: "2026-08-09",
      startNo: 500,
      endNo: 549,
      nextFree: 500,
    },
    {
      departmentKey: "AYUR",
      departmentName: "Ayurveda",
      date: "2026-08-09",
      startNo: 550,
      endNo: 599,
      nextFree: 550,
    },
  ]);
}

async function walkGenmed(opts: { ayurveda: boolean; problem?: string }) {
  const session = await startLocal({
    lang: "hi",
    chiefComplaint: "kamzori",
    caregiver: false,
    details: { name: "सीमा देवी", age: 54, sex: "female", phone: "", externalId: "" },
    departmentKey: "GENMED",
    departmentName: "General Medicine",
    departmentCareSystem: "allopathy",
  });
  session.walk.save("gm.problem", opts.problem ?? "weakness");
  session.walk.save("gm.duration", 5);
  session.walk.save("gm.severity", 4);
  session.walk.save("gm.ayur", opts.ayurveda ? "ayurveda" : "regular");
  session.walk.save("gm.words", "bas thakan rehti hai", { text: "bas thakan rehti hai" });
  return session;
}

test.afterEach(() => _setDb(null));

test.describe("offline: the department a walk asks for", () => {
  test("asking for ayurveda draws the token from the ayurveda block", async () => {
    await cachedKiosk();
    const session = await walkGenmed({ ayurveda: true });

    const confirmed = await confirmLocal(session);

    expect(confirmed.departmentKey).toBe("AYUR");
    expect(confirmed.departmentName).toBe("Ayurveda");
    expect(confirmed.departmentCareSystem).toBe("ayurveda");
    // AYUR's block starts at 550. A number from GENMED's 500-549 would sync
    // cleanly and put her on the wrong board.
    expect(confirmed.tokenNo).toBe(550);
    expect(confirmed.needsPaper).toBe(false);

    const queued = await pending();
    expect(queued).toHaveLength(1);
    expect(queued[0].departmentKey).toBe("AYUR");
    expect(queued[0].tokenNo).toBe(550);
    // The tree the questions came from is recorded truthfully — she answered
    // General Medicine's questions and is being seen in Ayurveda.
    expect(queued[0].treeKey).toBe("general_medicine_routing");
  });

  test("declining leaves her in the department she started in", async () => {
    await cachedKiosk();
    const session = await walkGenmed({ ayurveda: false });

    const confirmed = await confirmLocal(session);

    expect(confirmed.departmentKey).toBe("GENMED");
    expect(confirmed.tokenNo).toBe(500);
  });

  test("a red flag cancels the preference here too", async () => {
    // doc 24 §4. The offline walker has to reach the same answer as the server,
    // because during an outage it is the only one that runs.
    await cachedKiosk();
    const session = await walkGenmed({ ayurveda: true, problem: "breathing" });

    expect(session.walk.redFlags().length).toBeGreaterThan(0);
    const confirmed = await confirmLocal(session);

    expect(confirmed.departmentKey).toBe("GENMED");
    expect(confirmed.tokenNo).toBe(500);
  });

  test("a department this kiosk has never cached leaves her where she is", async () => {
    // The bundle is the kiosk's whole picture of which departments exist. One it
    // has never seen is not somewhere to send a patient on a guess.
    globalThis.indexedDB = new IDBFactory();
    _setDb(new KioskDb(`test-${Math.random().toString(36).slice(2)}`));
    await saveBundle({
      etag: "e1",
      fetchedAt: new Date().toISOString(),
      departments: [DEPARTMENTS[0]],
      trees: [canonicalTree("general_medicine_routing")],
    });
    await saveBlocks([
      {
        departmentKey: "GENMED",
        departmentName: "General Medicine",
        date: "2026-08-09",
        startNo: 500,
        endNo: 549,
        nextFree: 500,
      },
    ]);
    const session = await walkGenmed({ ayurveda: true });

    const confirmed = await confirmLocal(session);

    expect(confirmed.departmentKey).toBe("GENMED");
    expect(confirmed.tokenNo).toBe(500);
  });

  test("no block for the destination sends her to the desk, not to a made-up number", async () => {
    // doc 01 §5 step 3: never invent a token. The ayurveda block is missing
    // (leased before the department opened, say), so the honest answer is paper.
    globalThis.indexedDB = new IDBFactory();
    _setDb(new KioskDb(`test-${Math.random().toString(36).slice(2)}`));
    await saveBundle({
      etag: "e1",
      fetchedAt: new Date().toISOString(),
      departments: DEPARTMENTS,
      trees: [canonicalTree("general_medicine_routing")],
    });
    await saveBlocks([
      {
        departmentKey: "GENMED",
        departmentName: "General Medicine",
        date: "2026-08-09",
        startNo: 500,
        endNo: 549,
        nextFree: 500,
      },
    ]);
    const session = await walkGenmed({ ayurveda: true });

    const confirmed = await confirmLocal(session);

    expect(confirmed.tokenNo).toBeNull();
    expect(confirmed.needsPaper).toBe(true);
    expect(confirmed.departmentKey).toBe("AYUR");
    expect(await pending()).toHaveLength(0);
  });
});
