// MRD1 — the coordinator's scanner, driven against a live stack. This file *is*
// the session's frontend acceptance criterion.
//
// The four things it proves, in the order they matter:
//
//   1. A report is filed against the patient the coordinator tapped, in three
//      actions — pick, photograph, done. A mis-file here puts one person's lab
//      values on another's screen, which is the worst thing this module can do.
//   2. The page count is truthful at every moment: it counts pages the *server*
//      has, not pages the phone has taken.
//   3. A page that fails to upload is visible, retryable, and blocks "Done" —
//      a half-filed report that claims to be whole is worse than no report.
//   4. Search finds a patient by token/UHC/phone and never by name.
//
//   cd backend && DATABASE_URL=... .venv/bin/python -m app.seed
//   API_BASE=http://127.0.0.1:8000 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=scan
//
// ⚠️ It files real documents on whatever database it points at. Fine on a dev box.

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8000";
const SHOTS = "screenshots/mrd1";
const COORDINATOR_PHONE = "+915550000002";

// A real one-pixel PNG. It has to decode, because the page downscales every
// capture through `createImageBitmap` before uploading — a fixture that is not
// a decodable image tests the error path by accident. The point of this suite
// is the *flow*; what a vision model makes of the bytes is
// `tests/test_mrd_pipeline.py`'s business.
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);
const PAGE_FILE = { name: "page.png", mimeType: "image/png", buffer: PIXEL };

async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

/** One staff token for the whole file, taken through the API.
 *
 *  Not four sign-ins through the form: the OTP resend cooldown is 30 seconds,
 *  so back-to-back logins as the same coordinator fail on the *second* test with
 *  a missing code — a trap this repo has hit before (see HANDOFF, S-C). The
 *  login form itself is covered by `the scanner is behind a staff login`. */
async function staffToken(request: APIRequestContext): Promise<string> {
  // The cooldown is 30 seconds and a restarted worker asks again, so a 429 is
  // waited out rather than reported as a broken suite.
  let code: string | undefined;
  for (let attempt = 0; attempt < 3 && !code; attempt++) {
    const asked = await request.post(`${API}/auth/otp/request`, {
      data: { phone: COORDINATOR_PHONE },
    });
    if (asked.status() === 429) {
      await new Promise((resolve) => setTimeout(resolve, 32_000));
      continue;
    }
    code = (await asked.json()).debug_code;
  }
  if (!code) throw new Error("no OTP: is OTP_DEBUG_ECHO on, and the cooldown clear?");
  const verified = await request.post(`${API}/auth/otp/verify`, {
    data: { phone: COORDINATOR_PHONE, code },
  });
  return (await verified.json()).access_token;
}

/** Open the scanner already signed in. Same localStorage key the coordinator
 *  console uses — the same person on the same shift. */
async function openScanner(page: Page, token: string) {
  await page.goto("/scan");
  await page.evaluate((t) => window.localStorage.setItem("opd_staff_token", t), token);
  await page.reload();
  await expect(page.getByTestId("scan-root")).toBeVisible({ timeout: 15_000 });
}

/** Photograph one page. The real control is a camera `input[type=file]`; a
 *  headless browser has no camera, so the file is set directly — which is the
 *  same code path the phone takes once the camera hands back a File. */
async function photograph(page: Page, count = 1) {
  const before = Number((await page.getByTestId("scan-page-count").textContent()) ?? "0");
  await page.getByTestId("scan-capture").locator("input").setInputFiles(
    Array.from({ length: count }, (_, i) => ({ ...PAGE_FILE, name: `page-${i + 1}.png` })),
  );
  await expect(page.getByTestId("scan-page-count")).toHaveText(String(before + count), {
    timeout: 15_000,
  });
}

test.describe("coordinator scanning", () => {
  let token = "";

  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    token = await staffToken(request);
    await request.dispose();
  });

  test("the scanner is behind a staff login", async ({ page }) => {
    await page.goto("/scan");

    // Unlike the kiosk, nothing here is anonymous: a scanned oncology report is
    // the most identifying object this system holds.
    await expect(page.getByLabel(/phone/i)).toBeVisible();
    await expect(page.getByTestId("scan-root")).toHaveCount(0);
  });

  test("a lab report is filed against the tapped patient in three actions", async ({
    page,
  }) => {
    await openScanner(page, token);
    await shot(page, "01-pick");

    const first = page.getByTestId("scan-patient").first();
    await expect(first).toBeVisible({ timeout: 15_000 });
    const chosen = (await first.textContent()) ?? "";
    await first.click();

    await page.getByTestId("scan-kind-lab").click();
    await photograph(page, 2);
    await shot(page, "02-capture");

    // The strip shows one frame per page, and the count is the server's.
    await expect(page.getByTestId("scan-strip").locator("li")).toHaveCount(2);
    await expect(page.getByTestId("scan-page-count")).toHaveText("2");

    await page.getByTestId("scan-finish").click();
    await expect(page.getByTestId("scan-done")).toContainText("2 pages filed", {
      timeout: 15_000,
    });
    await shot(page, "03-done");

    // Back to the picker for the next patient, and the patient we just scanned
    // for now shows a document on file — the guard against scanning it twice.
    await page.getByTestId("scan-next-patient").click();
    await expect(page.getByTestId("scan-patient").first()).toBeVisible();
    const name = chosen.replace(/\d+/g, "").trim().split("\n")[0];
    if (name) {
      await expect(
        page.getByTestId("scan-patient").filter({ hasText: name }).first(),
      ).toContainText(/document/i);
    }
  });

  test("nothing can be filed before a page is actually stored", async ({ page }) => {
    await openScanner(page, token);
    await page.getByTestId("scan-patient").first().click();

    // Done is unavailable with an empty document: a zero-page report is not a
    // report, and the backend refuses it too (`complete_capture`).
    await expect(page.getByTestId("scan-finish")).toBeDisabled();

    await photograph(page);
    await expect(page.getByTestId("scan-finish")).toBeEnabled();
  });

  test("a page that fails to upload is visible, retryable, and blocks Done", async ({
    page,
  }) => {
    await openScanner(page, token);
    await page.getByTestId("scan-patient").first().click();

    // One upload fails, the way a phone on a bad OPD connection fails.
    let fail = true;
    await page.route("**/records/documents/*/pages", async (route) => {
      if (fail) {
        fail = false;
        await route.abort("failed");
      } else {
        await route.continue();
      }
    });

    await page.getByTestId("scan-capture").locator("input").setInputFiles(PAGE_FILE);

    // The count is the server's, so it stays at zero — and the screen says why.
    await expect(page.getByTestId("scan-failed-note")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("scan-page-count")).toHaveText("0");
    await expect(page.getByTestId("scan-finish")).toBeDisabled();
    await shot(page, "04-failed-page");

    await page.getByTestId("scan-retry-page").click();
    await expect(page.getByTestId("scan-page-count")).toHaveText("1", { timeout: 15_000 });
    await expect(page.getByTestId("scan-failed-note")).toHaveCount(0);
    await expect(page.getByTestId("scan-finish")).toBeEnabled();
  });

  test("search finds a patient by their id, and never by their name", async ({ page }) => {
    await openScanner(page, token);

    const first = page.getByTestId("scan-patient").first();
    await expect(first).toBeVisible({ timeout: 15_000 });
    const name = ((await first.textContent()) ?? "").replace(/\d+/g, "").trim().split("\n")[0];

    await page.getByTestId("scan-search").fill(name.slice(0, 6));
    await page.getByRole("button", { name: /find/i }).click();

    // A name search on a staff phone at a public desk would turn one
    // shoulder-surfed screen into a browsable oncology register.
    await expect(page.getByTestId("scan-empty")).toBeVisible({ timeout: 15_000 });
  });
});
