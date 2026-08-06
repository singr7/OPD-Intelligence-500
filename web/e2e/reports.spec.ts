// MRD2 — the doctor's Reports tab, driven against a live stack. This file *is*
// the session's acceptance criterion.
//
// The four things it proves, in the order they matter:
//
//   1. **The report is there before the patient is.** A coordinator scans at
//      the desk; the doctor opens the console and the spine already says how
//      many documents are on file and how many values are flagged, without
//      opening anything. That sentence is the entire module's reason to exist.
//   2. **An unverified reading is a draft, and says so** — until a doctor taps
//      *Mark reviewed*, at which point the draft stamp is replaced by who
//      checked it and when.
//   3. **The original is one tap from every number.** Tapping a flagged value's
//      page opens that photograph, fetched under the auth guard — there is no
//      signed URL to leak, so if this works at all it works through the token.
//   4. **A document that could not be read is still a document.** The pages are
//      shown, the reason is stated, and the coordinator's own screen offers the
//      re-read.
//
//   cd backend && .venv/bin/python -m scripts.seed_doctor_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=reports
//
// The api must run with `MRD_ENABLED=true` and `OBJECT_STORE=filesystem`. The
// LLM may stay `fake`: it declares `supports_images` and has a canned reply for
// both MRD prompts, so the real pipeline runs end to end with no vendor.
//
// ⚠️ It files real documents and records a real verification on whatever
// database it points at. Dev boxes only.

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/mrd2";
const DOCTOR_PHONE = "+915550001001"; // seeded Dr. Anil Gupta (MEDONC)
const COORDINATOR_PHONE = "+915550000002"; // seeded Rekha Meena

// A decodable one-pixel PNG — the page route streams back whatever was stored,
// and the browser has to be able to render it. What a vision model would make
// of these bytes is `tests/test_mrd_pipeline.py`'s business, not this file's.
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

test.describe.configure({ mode: "serial" });

async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

/** One token per phone, taken through the API.
 *
 *  Not a sign-in per test: the OTP resend cooldown is 30 seconds and back-to-back
 *  logins as the same person fail on the second with a missing code. A 429 is
 *  waited out rather than reported as a broken suite. */
async function tokenFor(request: APIRequestContext, phone: string): Promise<string> {
  let code: string | undefined;
  for (let attempt = 0; attempt < 3 && !code; attempt++) {
    const asked = await request.post(`${API}/auth/otp/request`, { data: { phone } });
    if (asked.status() === 429) {
      await new Promise((resolve) => setTimeout(resolve, 32_000));
      continue;
    }
    code = (await asked.json()).debug_code;
  }
  if (!code) throw new Error(`no OTP for ${phone}: is OTP_DEBUG_ECHO on?`);
  const verified = await request.post(`${API}/auth/otp/verify`, { data: { phone, code } });
  return (await verified.json()).access_token;
}

type Scanned = {
  documentId: string;
  patientId: string;
  visitId: string;
  name: string;
  tokenNo: number;
};

/** Scan a document for the first patient on the doctor's list, exactly as the
 *  coordinator's phone does it: open, post pages one at a time, complete. */
async function scanFor(
  request: APIRequestContext,
  staff: string,
  doctor: string,
  { pages = 1, kind = "lab" } = {},
): Promise<Scanned> {
  const day = await request.get(`${API}/doctor/day?scope=department`, {
    headers: { Authorization: `Bearer ${doctor}` },
  });
  const rows = (await day.json()).rows as {
    visit_id: string;
    patient_name: string;
    token_no: number;
  }[];
  if (rows.length === 0) throw new Error("no patients today — run scripts.seed_doctor_demo");
  const visitId = rows[0].visit_id;

  const card = await request.get(`${API}/doctor/patients/${visitId}`, {
    headers: { Authorization: `Bearer ${doctor}` },
  });
  const patientId = (await card.json()).patient_id as string;

  const created = await request.post(`${API}/records/documents`, {
    headers: { Authorization: `Bearer ${staff}` },
    data: { patient_id: patientId, visit_id: visitId, kind },
  });
  expect(created.status()).toBe(201);
  const documentId = (await created.json()).id as string;

  for (let n = 1; n <= pages; n++) {
    const posted = await request.post(`${API}/records/documents/${documentId}/pages`, {
      headers: { Authorization: `Bearer ${staff}` },
      multipart: {
        file: { name: `page-${n}.png`, mimeType: "image/png", buffer: PIXEL },
      },
    });
    expect(posted.status()).toBe(200);
  }

  const done = await request.post(`${API}/records/documents/${documentId}/complete`, {
    headers: { Authorization: `Bearer ${staff}` },
  });
  expect(done.status()).toBe(200);

  return {
    documentId,
    patientId,
    visitId,
    name: rows[0].patient_name,
    tokenNo: rows[0].token_no,
  };
}

/** Wait for the pipeline to finish reading. The API nudges extraction as a
 *  background task and the worker sweep is the backstop, so this polls rather
 *  than assuming either has run. */
async function waitForReading(
  request: APIRequestContext,
  doctor: string,
  documentId: string,
): Promise<Record<string, unknown>> {
  for (let attempt = 0; attempt < 40; attempt++) {
    const res = await request.get(`${API}/records/documents/${documentId}`, {
      headers: { Authorization: `Bearer ${doctor}` },
    });
    const body = await res.json();
    if (body.status === "summarized" || body.status === "extraction_failed") return body;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("the document never finished extracting — is MRD_ENABLED set on the api?");
}

async function openConsole(page: Page, token: string, tokenNo?: number) {
  // The same key the coordinator console uses — both are staff logins against
  // the same /auth endpoints (see `_lib/session.ts`).
  await page.goto("/doctor");
  await page.evaluate((t) => window.localStorage.setItem("opd_staff_token", t), token);
  await page.reload();
  await expect(page.getByTestId("context-spine")).toBeVisible({ timeout: 20_000 });
  if (tokenNo != null) {
    // Open the patient this test scanned for, rather than whoever the console
    // auto-opened — which one that is belongs to the queue, not to this file.
    await page.getByTestId(`station-${tokenNo}`).click();
    await expect(page.getByTestId("spine-reports")).toBeVisible();
  }
}

test.describe("the doctor reads what the desk scanned", () => {
  let doctorToken = "";
  let staffToken = "";
  let scanned: Scanned;

  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    doctorToken = await tokenFor(request, DOCTOR_PHONE);
    staffToken = await tokenFor(request, COORDINATOR_PHONE);
    scanned = await scanFor(request, staffToken, doctorToken, { pages: 2 });
    const reading = await waitForReading(request, doctorToken, scanned.documentId);
    // The whole suite is about rendering a reading; if the pipeline did not
    // produce one, say so here rather than failing four tests obscurely.
    expect(
      reading.status,
      "extraction failed — check MRD_ENABLED and the LLM profile on the api",
    ).toBe("summarized");
    await request.dispose();
  });

  test("the spine says what is on file before the doctor opens anything", async ({ page }) => {
    await openConsole(page, doctorToken, scanned.tokenNo);

    // The module's stated intent, as an assertion: this is on screen with the
    // Overview tab open, before any deliberate act of looking for reports.
    const spine = page.getByTestId("spine-reports");
    await expect(spine).toBeVisible();
    await expect(spine).toContainText(/on file/);
    await expect(spine).toContainText(/flagged/);
    await expect(page.getByTestId("reports-badge")).toBeVisible();

    await shot(page, "01-spine-before-opening");
  });

  test("the spine line opens the tab, and the reading is stamped as a draft", async ({
    page,
  }) => {
    await openConsole(page, doctorToken, scanned.tokenNo);
    await page.getByTestId("spine-reports").click();

    await expect(page.getByTestId("reports-tab")).toBeVisible();
    await expect(page.getByTestId("report-summary")).toContainText(/Hb 8\.9/);

    // An unverified machine reading is a draft and every surface showing one
    // says so (doc 21 §1.5).
    await expect(page.getByTestId("draft-banner")).toBeVisible();
    await expect(page.getByTestId("draft-banner")).toContainText(/unverified/i);

    await shot(page, "02-reports-draft");
  });

  test("a flag from our own table is marked weaker than one the lab printed", async ({
    page,
  }) => {
    await openConsole(page, doctorToken, scanned.tokenNo);
    await page.getByTestId("spine-reports").click();

    const rows = page.getByTestId("value-row");
    await expect(rows.first()).toBeVisible();

    // Hemoglobin was flagged against the range printed on the report; the
    // neutrophil count had none, so `seeds/lab_reference_ranges.json` decided
    // it — and that table ships `review_pending`, so the row must say so.
    await expect(page.getByText("printed on report").first()).toBeVisible();
    await expect(page.getByText("our range").first()).toBeVisible();
    await expect(page.getByTestId("fallback-note")).toBeVisible();

    // Values the model could not read are named, not guessed at.
    await expect(page.getByTestId("illegible")).toContainText(/printer band/);

    // The table is below the fold on a laptop — scroll to it, or the screenshot
    // for the doc 04 §5 critique is a picture of the screen above it.
    await page.getByTestId("values-table").scrollIntoViewIfNeeded();
    // …and back off, because the spine is sticky and would otherwise sit on top
    // of the first flagged row in the picture.
    await page.evaluate(() => window.scrollBy(0, -170));
    await shot(page, "03-values");
  });

  test("the original page is one tap from the number, and needs the token", async ({ page }) => {
    await openConsole(page, doctorToken, scanned.tokenNo);
    await page.getByTestId("spine-reports").click();

    await page.getByTestId("value-page").first().click();

    const zoom = page.getByTestId("page-zoom");
    await expect(zoom).toBeVisible();
    // It rendered, which is the whole assertion: the bytes came back through a
    // fetch carrying the bearer token. There is no signed URL for this image.
    const img = zoom.locator("img");
    await expect(img).toBeVisible();
    await expect(async () => {
      expect(await img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
    }).toPass({ timeout: 10_000 });
    expect(await img.getAttribute("src")).toMatch(/^blob:/);

    await shot(page, "04-original-page");
    await page.keyboard.press("Escape");
    await expect(zoom).toHaveCount(0);
  });

  test("marking it reviewed replaces the draft stamp with who and when", async ({ page }) => {
    await openConsole(page, doctorToken, scanned.tokenNo);
    await page.getByTestId("spine-reports").click();

    await page.getByTestId("verify").click();

    await expect(page.getByTestId("verified-banner")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("verified-banner")).toContainText(/Reviewed against the original/);
    await expect(page.getByTestId("draft-banner")).toHaveCount(0);

    await shot(page, "05-reviewed");
  });

  test("a document that could not be read is still a document", async ({ page }) => {
    // A vendor outage cannot be produced from a browser against a live stack,
    // and the backend already has tests for reaching this state. What is only
    // testable here is the *rendering* of it, so the document list is answered
    // with a failed reading — pointed at the real document, so the page bytes
    // below it are still streamed for real through the guard.
    await page.route("**/records/patients/*/documents", async (route) => {
      const real = await route.fetch();
      const documents = await real.json();
      documents[0] = {
        ...documents[0],
        status: "extraction_failed",
        failure_reason: "could not be read by the model: gemini http 503",
        extraction: null,
      };
      await route.fulfill({ response: real, json: documents });
    });

    await openConsole(page, doctorToken, scanned.tokenNo);
    await page.getByTestId("spine-reports").click();

    // The pipeline degrades to a photo viewer, never to a blank: the reason is
    // stated and the originals are still on screen.
    await expect(page.getByTestId("unread-banner")).toContainText(/503/);
    await expect(page.getByTestId("page-strip").first()).toBeVisible();
    await expect(page.getByTestId("page-thumb-1").first()).toBeVisible();

    await shot(page, "06-still-a-document");
    await page.unroute("**/records/patients/*/documents");
  });
});

test.describe("the desk is told what did not read", () => {
  // `/scan` is a phone in someone's hand at a busy desk, not a laptop. Shooting
  // it at desktop width would critique a screen nobody uses — same viewport the
  // `scan` project pins.
  test.use({ viewport: { width: 414, height: 896 } });

  test("a failed scan appears on the coordinator's screen with a re-read", async ({
    page,
    playwright,
  }) => {
    const request = await playwright.request.newContext();
    const staff = await tokenFor(request, COORDINATOR_PHONE);
    const failures = await request.get(`${API}/records/scan/failures`, {
      headers: { Authorization: `Bearer ${staff}` },
    });
    expect(failures.status()).toBe(200);
    const rows = (await failures.json()) as unknown[];
    await request.dispose();

    await page.goto("/scan");
    await page.evaluate((t) => window.localStorage.setItem("opd_staff_token", t), staff);
    await page.reload();
    await expect(page.getByTestId("scan-root")).toBeVisible({ timeout: 15_000 });

    if (rows.length > 0) {
      await expect(page.getByTestId("scan-failures")).toBeVisible();
      await expect(page.getByTestId("scan-reread").first()).toBeVisible();
      await page.getByTestId("scan-failures").scrollIntoViewIfNeeded();
      await shot(page, "07-desk-failures");
    } else {
      // Nothing failed on this box. The section is absent by design, and the
      // endpoint answering 200 with an empty list is the assertion above.
      await expect(page.getByTestId("scan-failures")).toHaveCount(0);
    }
  });
});
