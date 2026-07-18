import { expect, test, type Page, type Route } from "@playwright/test";
import path from "node:path";

// The S7 acceptance criteria, in a browser (doc 06 S7):
//
//   kill the API for 10 minutes → the kiosk completes three offline intakes with
//   valid tokens → restart → all sync, zero collisions.
//
// The service-layer proof lives in backend/tests/test_offline.py (the same three
// intakes through the real /kiosk/sync). This adds the half that only a browser
// can show: that an intake actually *completes* against the ported walker with
// the API unreachable, queues locally, and reconciles when the API returns.
//
// The API is "killed" by aborting every request to it while leaving the web
// server up — which is exactly the failure the pilot expects (the api container
// stops; the kiosk PWA keeps being served). See HANDOFF S6: a dead api reads to
// the browser as a failed fetch, and the kiosk must treat that as downtime, not
// as an error to show the patient.

const SHOTS = path.join(__dirname, "..", "screenshots", "s7");
const API = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8123";

async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  // Bounded and non-fatal: with the API killed, a pending font/asset request can
  // make screenshot()'s font wait hang, and a missing screenshot must not fail
  // the actual proof (the token + sync assertions below).
  await page
    .screenshot({ path: path.join(SHOTS, `${name}.png`), animations: "disabled", timeout: 6000 })
    .catch(() => {});
}

/** Abort every call to the API host — the outage. */
async function killApi(page: Page): Promise<void> {
  await page.route(`${API}/**`, (route: Route) => route.abort());
}

async function reviveApi(page: Page): Promise<void> {
  await page.unroute(`${API}/**`);
}

async function typeInto(page: Page, text: string) {
  const toggle = page.getByTestId("type-toggle");
  if (await toggle.count()) await toggle.click();
  await page.getByRole("textbox").fill(text);
}

async function walkOneOfflineIntake(page: Page): Promise<number> {
  // From welcome → token, entirely offline. Returns the token shown.
  await page.getByTestId("welcome-lang-hi").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "caregiver");
  await page.getByText("मैं अपने लिए").click();

  await expect(page.locator("main")).toHaveAttribute("data-screen", "complaint");
  await typeInto(page, "mujhe pet mein dard hai");

  // Offline there is no classifier, so Next drops us on the chooser.
  await page.getByTestId("cc-next").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "chooser", {
    timeout: 15_000,
  });
  await page.getByTestId("option").filter({ hasText: "Medical Oncology" }).click();

  await expect(page.locator("main")).toHaveAttribute("data-screen", "question", {
    timeout: 15_000,
  });

  for (let i = 0; i < 40; i++) {
    const screen = await page.getAttribute("main", "data-screen");
    if (screen !== "question") break;
    const type = (await page.getAttribute("main", "data-node-type")) ?? "";
    if (type === "single") {
      await page.getByTestId("option").first().click();
    } else if (type === "multi" || type === "body_map") {
      await page.getByTestId("option").first().click();
      await page.getByTestId("answer-submit").click();
    } else if (type === "scale") {
      await page.getByTestId("face").nth(3).click();
      await page.getByTestId("answer-submit").click();
    } else if (type === "number") {
      await page.getByTestId("answer-submit").click();
    } else if (type === "free_voice") {
      await typeInto(page, "bahut dard hai");
      await page.getByTestId("answer-submit").click();
    }
    await page.waitForTimeout(200);
  }

  await expect(page.locator("main")).toHaveAttribute("data-screen", "readback", {
    timeout: 15_000,
  });
  await page.getByTestId("confirm").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "token", {
    timeout: 15_000,
  });

  const tokenText = (await page.getByTestId("token-number").textContent()) ?? "";
  const token = parseInt(tokenText.replace(/\D/g, ""), 10);
  expect(Number.isFinite(token)).toBeTruthy();
  return token;
}

test("kiosk completes three intakes offline, then syncs with zero collisions", async ({
  page,
}) => {
  // The demo cannot wait the full 60s for the downtime banner.
  await page.addInitScript(() => {
    window.__KIOSK_DOWNTIME_AFTER_MS__ = 1200;
  });

  // 1. Boot online: cache the bundle, lease the token blocks.
  await page.goto("/kiosk");
  await expect(page.locator("main")).toHaveAttribute("data-screen", "welcome");
  // Give the boot effect a moment to cache + lease before the outage.
  await page.waitForTimeout(2500);

  // 2. The API goes down. Nudge an immediate probe rather than waiting for the
  //    15s heartbeat (a real kiosk would flip within a heartbeat + the 60s
  //    threshold; the test cannot wait that long).
  await killApi(page);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByTestId("downtime-banner")).toBeVisible({ timeout: 10_000 });
  await shot(page, "01-downtime-welcome");

  // 3. Three patients complete an intake with no server.
  const tokens: number[] = [];
  for (let n = 0; n < 3; n++) {
    const token = await walkOneOfflineIntake(page);
    tokens.push(token);
    if (n === 0) await shot(page, "02-offline-token");
    // Back to the top for the next patient.
    await page.getByTestId("token-done").click();
    await expect(page.locator("main")).toHaveAttribute("data-screen", "welcome");
  }

  // Every token is from the offline block (>= base) and distinct.
  expect(tokens.every((t) => t >= 500), `tokens were ${JSON.stringify(tokens)}`).toBeTruthy();
  expect(new Set(tokens).size).toBe(3);

  // 4. The API comes back. Nudge the monitor (the 'online' event is what a real
  //    reconnect fires) and let sync run.
  await reviveApi(page);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));

  // 5. Every queued intake reaches the server and is marked synced locally —
  //    the full round trip through the real /kiosk/sync.
  await expect
    .poll(
      () =>
        page.evaluate(async () => {
          const req = indexedDB.open("opd-kiosk");
          const db: IDBDatabase = await new Promise((res, rej) => {
            req.onsuccess = () => res(req.result);
            req.onerror = () => rej(req.error);
          });
          const rows: { status: string }[] = await new Promise((res, rej) => {
            const tx = db.transaction("queue", "readonly");
            const all = tx.objectStore("queue").getAll();
            all.onsuccess = () => res(all.result);
            all.onerror = () => rej(all.error);
          });
          db.close();
          return {
            total: rows.length,
            pending: rows.filter((r) => r.status === "pending").length,
            synced: rows.filter((r) => r.status === "synced").length,
          };
        }),
      { timeout: 20_000, message: "all offline intakes should sync" }
    )
    .toEqual({ total: 3, pending: 0, synced: 3 });
  // A rejection (a collision, a token outside the block) would leave the intake
  // pending, not synced — so "3 synced, 0 pending" is the zero-collision proof,
  // observed through the real /kiosk/sync round trip. The service-layer test
  // asserts the same three tokens land as distinct visits server-side.

  await shot(page, "03-synced-welcome");
});
