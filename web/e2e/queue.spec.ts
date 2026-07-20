// S8 queue board + coordinator console (doc 03 §6), driven against a live stack.
// Proves the two AC behaviours a unit test can't: the board and console stay in
// sync over the WebSocket, and an urgent red-flag token shows its reason chip on
// both surfaces. Also captures the screenshots for the doc 04 §5 self-critique.
//
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=queue

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/s8";
const COORDINATOR_PHONE = "+915550000002"; // seeded coordinator

test.describe.configure({ mode: "serial" });

async function loginToken(request: import("@playwright/test").APIRequestContext): Promise<string> {
  const req = await request.post(`${API}/auth/otp/request`, {
    data: { phone: COORDINATOR_PHONE },
  });
  const code = (await req.json()).debug_code as string;
  const ver = await request.post(`${API}/auth/otp/verify`, {
    data: { phone: COORDINATOR_PHONE, code },
  });
  return (await ver.json()).access_token as string;
}

test("board shows the urgent token jumped to now-serving with its reason", async ({ page }) => {
  await page.goto("/board");
  await expect(page.locator(".serving-num").first()).toBeVisible();
  // The demo seeds a red-flag walk-in in the first room; it jumped and was called.
  await expect(page.locator(".urgent-chip").first()).toContainText("Fever after chemo");
  await page.waitForTimeout(700); // let the numeral flip settle for a crisp shot
  await page.screenshot({ path: `${SHOTS}/board.png`, fullPage: true });
});

test("coordinator logs in and sees the queue with the urgent chip", async ({ page }) => {
  await page.goto("/coordinator");
  await expect(page.locator(".login h1")).toHaveText("Sign in");
  await page.screenshot({ path: `${SHOTS}/coordinator-login.png` });

  await page.click("button[type=submit]"); // Send code
  const hint = await page.locator(".hint").textContent();
  const code = (hint ?? "").replace(/\D/g, "");
  await page.fill("#code", code);
  await page.click("button[type=submit]"); // Sign in

  await expect(page.locator(".appbar strong")).toHaveText("Coordinator");
  await expect(page.locator(".chip-urgent").first()).toContainText("Fever after chemo");
  await page.screenshot({ path: `${SHOTS}/coordinator-queue.png`, fullPage: true });
});

test("entering downtime repaints the app bar marigold and raises the banner", async ({
  page,
  request,
}) => {
  const token = await loginToken(request);
  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/coordinator");
  // Let the queue settle before interacting so the app bar isn't mid-relayout.
  await expect(page.locator(".dept, .empty-state").first()).toBeVisible();

  // Click the real toggle — this is the coordinator's control, not an API poke.
  await page.getByRole("button", { name: "Enter downtime" }).click();
  await expect(page.locator(".downtime-banner")).toBeVisible();
  await expect(page.locator(".console.is-downtime")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/coordinator-downtime.png`, fullPage: true });

  // Exit downtime so the demo state is clean for a re-run.
  await page.getByRole("button", { name: "Exit downtime" }).click();
  await expect(page.locator(".downtime-banner")).toHaveCount(0);
});

test("paper-entry and reconciliation tabs render", async ({ page, request }) => {
  const token = await loginToken(request);
  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/coordinator");

  await page.click("nav.tabs button:has-text('Paper entry')");
  await expect(page.locator(".paper-form h2")).toHaveText("Enter a paper intake");
  await page.screenshot({ path: `${SHOTS}/coordinator-paper.png`, fullPage: true });

  await page.click("nav.tabs button:has-text('Reconciliation')");
  // Either a table or the empty state — both are valid; just prove the tab loads.
  await expect(page.locator(".recon, .empty-state")).toBeVisible();
});

test("board updates live when the console calls the next token", async ({ page, request }) => {
  await page.goto("/board");
  const before = await page.locator(".serving-num").first().textContent();

  // Call next in the first department via the API (as the console would).
  const token = await loginToken(request);
  const board = await (await request.get(`${API}/queue/board`)).json();
  const firstDept = board.departments[0].department_key;
  await request.post(`${API}/queue/call-next`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { department_key: firstDept },
  });

  // The board holds a WS; it should re-fetch and change without a manual reload.
  await expect
    .poll(async () => (await page.locator(".serving-num").first().textContent())?.trim(), {
      timeout: 8000,
    })
    .not.toBe((before ?? "").trim());
});
