// S18-late admin console (doc 03 §10), driven against a live stack.
//
// This file is the session's acceptance criterion as a test: a non-technical
// user opens the tree editor, changes the words on an option, publishes, and a
// patient starting an intake at the kiosk reads the new words — no deploy, no
// re-seed, no JSON. It also captures the doc 04 §5 screenshots for the editor
// and the protocol panel.
//
//   cd backend && DATABASE_URL=... .venv/bin/python -m app.seed
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=admin

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/s18l";
const ADMIN_PHONE = "+915550000001"; // seeded Priya Sharma (admin)
let cachedAccessToken: string | null = null;

// The general-medicine walk-in tree: five plain options, one of which is the
// breathlessness red flag — so it exercises both halves of the editor.
const TREE = "general_medicine_routing";
// Unique per run: the suite publishes its edit, so a fixed string would be
// already-present on the next run and typing it would change nothing.
const EDITED = `Weakness or tiredness (edited ${Date.now()})`;

test.describe.configure({ mode: "serial" });

async function loginToken(request: import("@playwright/test").APIRequestContext): Promise<string> {
  if (cachedAccessToken) return cachedAccessToken;
  const req = await request.post(`${API}/auth/otp/request`, { data: { phone: ADMIN_PHONE } });
  const code = (await req.json()).debug_code as string;
  const ver = await request.post(`${API}/auth/otp/verify`, {
    data: { phone: ADMIN_PHONE, code },
  });
  cachedAccessToken = (await ver.json()).access_token as string;
  return cachedAccessToken;
}

/** Open the editor on the newest version of the tree. `list_trees` orders
 *  version-descending within a key, so the first matching row is the newest —
 *  and this suite publishes a new version each time it runs. */
async function openNewestVersion(page: import("@playwright/test").Page) {
  await page
    .locator(`tr:has-text('${TREE}') button:has-text('Edit')`)
    .first()
    .click();
  await expect(page.locator(".spine .station").first()).toBeVisible();
}

async function signedIn(page: import("@playwright/test").Page, token: string) {
  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/admin");
  await expect(page.locator(".admin header h1")).toHaveText("OPD control room");
}

test("the console signs an admin in and opens the tree editor", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));
  await page.click("nav button:has-text('Trees')");
  await expect(page.locator("table")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-trees-list.png`, fullPage: true });

  await openNewestVersion(page);

  // The spine: questions in ask order, branches indented under the option that
  // leads to them, red-flag stations stamped.
  await expect(page.locator(".spine .station").first()).toBeVisible();
  await expect(page.locator(".spine .station.flagged .stamp").first()).toHaveText("urgent");
  await page.screenshot({ path: `${SHOTS}/02-tree-editor.png`, fullPage: true });
});

test("editing an option, publishing, and the kiosk serves it — no deploy", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));
  await page.click("nav button:has-text('Trees')");
  await openNewestVersion(page);

  // 1. Change the words on an option, the way a clinic person would.
  const option = page.locator('.station[data-node="gm.problem"] .opt', {
    hasText: "weakness",
  });
  await option.locator("input").fill(EDITED);

  // 2. Save. A save is a new draft version — nothing is live yet.
  await page.click("button:has-text('Save as new draft')");
  await expect(page.locator(".editor-note")).toContainText("Nothing is live until you publish");
  await page.screenshot({ path: `${SHOTS}/03-saved-draft.png`, fullPage: true });

  // 3. Publish.
  await page.click("button:has-text('Publish v')");
  await expect(page.locator(".editor-note")).toContainText("is live");
  await page.screenshot({ path: `${SHOTS}/04-published.png`, fullPage: true });

  // 4. The acceptance criterion: a patient starting an intake now reads the edit.
  //    Asked of the intake path itself (the same call the kiosk makes), so this
  //    proves the server serves it rather than that the console remembers it.
  const started = await request.post(`${API}/kiosk/start`, {
    data: { lang: "en", chief_complaint: "fever since two days", dept_key: "GENMED" },
  });
  expect(started.ok()).toBeTruthy();
  const opened = await started.json();
  expect(opened.status).toBe("routed");
  const labels = (opened.node?.options ?? []).map((o: { text: string }) => o.text);
  expect(labels).toContain(EDITED);
});

test("the try-it panel walks the edited tree and raises its red flag", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));
  await page.click("nav button:has-text('Trees')");
  await openNewestVersion(page);

  // Answer as a breathless patient would; the deterministic walker — not the
  // editor — decides that this is urgent.
  await page.selectOption('.try:has-text("What is troubling you most") select', "breathing");
  await page.click(".testrun button:has-text('Run')");

  await expect(page.locator(".try-out .stamp").first()).toHaveText("urgent");
  await page.screenshot({ path: `${SHOTS}/05-test-run.png`, fullPage: true });
});

test("the protocol bank panel shows the live check-in bank", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));
  await page.click("nav button:has-text('Protocols')");
  await expect(
    page.getByRole("heading", { name: "Check-in protocol bank" }),
  ).toBeVisible();
  await expect(page.locator("table").first()).toContainText("Platinum");
  await page.screenshot({ path: `${SHOTS}/06-protocols.png`, fullPage: true });
});
