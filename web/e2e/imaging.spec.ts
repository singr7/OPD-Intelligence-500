// M3 — the PACS stub, driven against a live stack. This file *is* the session's
// frontend acceptance criterion.
//
// The four things it proves, in the order they matter:
//
//   1. **An empty list always says why it is empty.** This is the module. A
//      doctor told "no imaging on file" when the truth is "we could not reach
//      the imaging centre" has been told something false about their patient,
//      and the seeded day contains a patient with no UHC ID precisely so the
//      second state is reachable on a demo box.
//   2. **The studies are there, with the date, modality and series count**, and
//      the spine says so before the doctor opens anything.
//   3. **The viewer handoff carries a study UID and nothing else** — no token,
//      no patient identifier — and opens with `noopener`.
//   4. **Imaging renders even when there is no scanned paper.** A patient whose
//      documents have not been photographed may well have had three CTs, and an
//      earlier build returned before the section for exactly those patients.
//
//   cd backend && .venv/bin/python -m scripts.seed_doctor_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=imaging
//
// The api needs `PACS_ENABLED=true` and `PACS_PROVIDER=fake`. The fake's
// `demo()` answers for any UHC ID with two studies, so the whole module is
// demonstrable with no imaging centre attached — the MRD2 habit, because a
// module nobody can see is a module nobody reviews.
//
// **What this cannot prove** is that a real Orthanc answers the way the
// provider expects. That is the manual acceptance checklist in the session log
// (plan §2.2), and it is still outstanding.
//
// ⚠️ It writes audit rows on whatever database it points at. Dev boxes only.

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/m3";
const DOCTOR_PHONE = "+915550001001"; // seeded Dr. Anil Gupta (MEDONC)

/** The seeded patient deliberately left without a UHC ID, so the state a real
 *  clinic hits on its first week is reachable here. */
const NO_UHC_TOKEN = 14;

test.describe.configure({ mode: "serial" });

async function shot(page: Page, name: string, anchor = "imaging-section") {
  // Viewport and scrolled — the M5 lesson. `fullPage` renders sticky elements
  // where nobody sees them, and an unscrolled 720px viewport is all console.
  await page.evaluate((id) => {
    const target = document.querySelector(`[data-testid="${id}"]`);
    const spine = document.querySelector('[data-testid="context-spine"]');
    if (!target) return;
    const top = target.getBoundingClientRect().top + window.scrollY;
    window.scrollTo(0, top - (spine?.getBoundingClientRect().height ?? 0) - 56);
  }, anchor);
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

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

type Row = { visit_id: string; token_no: number; patient_name: string };

async function day(request: APIRequestContext, doctor: string): Promise<Row[]> {
  const resp = await request.get(`${API}/doctor/day?scope=department`, {
    headers: { Authorization: `Bearer ${doctor}` },
  });
  const rows = (await resp.json()).rows as Row[];
  expect(rows.length, "the seeded day has nobody on it — run seed_doctor_demo").toBeGreaterThan(0);
  return rows;
}

async function openImaging(page: Page, token: string, tokenNo: number) {
  await page.goto("/doctor", { waitUntil: "domcontentloaded" });
  await page.evaluate((t) => window.localStorage.setItem("opd_staff_token", t), token);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("context-spine")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId(`station-${tokenNo}`).click();
  await expect(page.getByTestId("context-spine")).toBeVisible();
  await page.getByTestId("tab-reports").click();
  await expect(page.getByTestId("imaging-section")).toBeVisible({ timeout: 20_000 });
}

test.describe("the scans are somewhere else", () => {
  let doctorToken = "";
  let rows: Row[] = [];

  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    doctorToken = await tokenFor(request, DOCTOR_PHONE);
    rows = await day(request, doctorToken);
    await request.dispose();
  });

  test("the studies are listed, and the spine said so first", async ({ page }) => {
    const patient = rows.find((r) => r.token_no !== NO_UHC_TOKEN)!;

    await page.goto("/doctor", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => window.localStorage.setItem("opd_staff_token", t), doctorToken);
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("context-spine")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId(`station-${patient.token_no}`).click();

    // 2. Before the doctor opens anything. This is the plan's `Images (n)`,
    // riding on the Reports line rather than taking a sixth spine slot.
    const spineImaging = page.getByTestId("spine-imaging");
    await expect(spineImaging).toBeVisible({ timeout: 20_000 });
    await expect(spineImaging).toContainText(/\d+ imaging stud(y|ies)/);
    // Never the word the scanned-paper tally uses, forty pixels to its left.
    await expect(spineImaging).not.toContainText(/\bscans?\b/);

    await page.getByTestId("tab-reports").click();
    const section = page.getByTestId("imaging-section");
    await expect(section).toBeVisible();

    const studies = page.getByTestId("imaging-study");
    await expect(studies).toHaveCount(2);
    await expect(studies.first()).toContainText("CT");
    await expect(studies.first()).toContainText("2026-07-30");
    await expect(studies.first()).toContainText("4 series");

    await shot(page, "01-studies");
  });

  test("the viewer handoff carries a study UID and nothing else", async ({ page }) => {
    const patient = rows.find((r) => r.token_no !== NO_UHC_TOKEN)!;
    await openImaging(page, doctorToken, patient.token_no);

    // 3. The URL is the server's, not the console's. Checked as a property of
    // the rendered href rather than of the payload, because this is the one
    // string in the module that leaves for another origin.
    const open = page.getByTestId("imaging-open").first();
    const href = await open.getAttribute("href");
    expect(href, "no viewer URL on the study").toBeTruthy();
    expect(href!).toContain("StudyInstanceUIDs=");
    expect(href!).not.toContain("token");
    expect(href!.toLowerCase()).not.toContain(patient.patient_name.split(" ")[0].toLowerCase());
    expect(href!).not.toMatch(/UHC\d/);

    // A popup that keeps a handle on this window can navigate the console it
    // came from. `noopener` is not optional here.
    expect(await open.getAttribute("rel")).toContain("noopener");
    expect(await open.getAttribute("target")).toBe("_blank");
  });

  test("a patient with no UHC ID is told that, not that they have no scans", async ({ page }) => {
    // 1. The module. The seed leaves token 14 without a UHC ID on purpose.
    await openImaging(page, doctorToken, NO_UHC_TOKEN);

    const empty = page.getByTestId("imaging-empty-no_uhc_id");
    await expect(empty).toBeVisible();
    await expect(empty).toContainText(/no uhc id/i);
    await expect(empty).toContainText(/desk can add one/i);

    // The distinction this whole module exists for: it must not have said the
    // patient has no scans, because nobody asked.
    await expect(page.getByTestId("imaging-section")).not.toContainText(/no scans on file/i);
    await expect(page.getByTestId("imaging-list")).toHaveCount(0);

    // And the spine says the same thing, in fewer words.
    await expect(page.getByTestId("spine-imaging")).toContainText(/cannot be looked up/i);

    await shot(page, "02-no-uhc-id");
  });

  test("an unreachable imaging server is not a statement about the patient", async ({ page }) => {
    const patient = rows.find((r) => r.token_no !== NO_UHC_TOKEN)!;

    // Produced at the browser: the backend's own unreachable path is proven in
    // `test_imaging_routes.py`, and what cannot be proven there is what the
    // *section* says when it meets one. Narrow — the status code and nothing
    // else.
    await page.route("**/imaging/visits/**", async (route) => {
      if (route.request().url().includes("/report")) return route.fallback();
      await route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({ detail: "pacs http 502" }),
      });
    });

    await openImaging(page, doctorToken, patient.token_no);

    const empty = page.getByTestId("imaging-empty-unreachable");
    await expect(empty).toBeVisible();
    await expect(empty).toContainText(/could not be reached/i);
    await expect(empty).toContainText(/not a statement that there are no scans/i);
    // The vendor's own words are not a thing to show a doctor (the M5 finding).
    await expect(empty).not.toContainText(/502|http/i);

    await shot(page, "03-unreachable");
  });

  test("the note dock's mic does not sit on a study's buttons", async ({ page }) => {
    // The M5 lesson, and the M5 fix: the dock is pinned to the right edge, so
    // the property that holds at every scroll position is horizontal
    // clearance. A vertical check is a check of one accident, and these rows
    // scroll.
    const patient = rows.find((r) => r.token_no !== NO_UHC_TOKEN)!;
    await openImaging(page, doctorToken, patient.token_no);

    const dock = await page.locator(".nd-fab-wrap").boundingBox();
    expect(dock, "the note dock is not mounted — has it moved?").not.toBeNull();

    for (const position of ["top", "middle", "bottom"] as const) {
      await page.evaluate((where) => {
        const max = document.body.scrollHeight - window.innerHeight;
        window.scrollTo(0, where === "top" ? 0 : where === "middle" ? max / 2 : max);
      }, position);
      await page.waitForTimeout(200);

      for (const testid of ["imaging-open", "imaging-report"]) {
        const box = await page.getByTestId(testid).last().boundingBox();
        expect(box, `no ${testid} on screen`).not.toBeNull();
        expect(
          box!.x + box!.width,
          `the mic dock overlapped ${testid} at scroll ${position}`,
        ).toBeLessThanOrEqual(dock!.x + 1);
      }
    }
  });

  test("imaging renders even for a patient with no scanned paper", async ({ page }) => {
    // 4. The bug an earlier build had: the Reports tab returned early when a
    // patient had no photographed documents, which hid the scans of exactly the
    // patients whose paperwork was not yet done.
    const patient = rows.find((r) => r.token_no !== NO_UHC_TOKEN)!;
    await openImaging(page, doctorToken, patient.token_no);

    const noPaper = page.getByTestId("reports-empty");
    if ((await noPaper.count()) > 0) {
      await expect(noPaper).toBeVisible();
    }
    // Either way the section is on screen with its studies.
    await expect(page.getByTestId("imaging-section")).toBeVisible();
    await expect(page.getByTestId("imaging-study").first()).toBeVisible();
  });
});
