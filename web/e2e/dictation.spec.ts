// S10 dictation → structured mapping (doc 03 §7), driven against a live stack.
//
// This is the session's acceptance criterion as a test. The ten Hinglish
// fixtures prove the mapping layer in `backend/tests/test_dictation.py`; what
// this file proves is the thing a unit test cannot: that a doctor sitting in
// front of the console *sees* the flag, cannot sign past it by accident, and
// gets their own words back rather than a tidied-up version of them.
//
//   cd backend && .venv/bin/python -m scripts.seed_doctor_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=dictation
//
// The stack runs with the default `LLM_PROVIDER=fake`, whose canned reply for
// `dictation_map` deliberately includes one off-formulary drug — a demo where
// nothing is ever flagged teaches the wrong thing about this screen.

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/s10";
const DOCTOR_PHONE = "+915550001001"; // seeded Dr. Anil Gupta (MEDONC)
let cachedAccessToken: string | null = null;

// The note a doctor would actually speak at the end of this consult.
const NOTE =
  "Breast carcinoma post cycle three, do din se fever hai, neutropenia hai. " +
  "Start karo Inj Monocef one gram IV BD five days, aur Tab Dolo 650 SOS. " +
  "Inj Ipilimumab 3 mg per kg three weekly. Next cycle 14 tareekh ko.";

test.describe.configure({ mode: "serial" });

// Each test works a *different* patient from the morning. Signing is terminal —
// a signed note refuses to be re-dictated, by design — so tests that shared one
// patient would pass once and then 409 for anyone who ran them twice without
// re-seeding. One row each keeps the file re-runnable.
const ROW = { opens: 0, maps: 1, flags: 2, acknowledges: 3, signs: 4 };

async function loginToken(request: import("@playwright/test").APIRequestContext): Promise<string> {
  if (cachedAccessToken) return cachedAccessToken;
  const req = await request.post(`${API}/auth/otp/request`, { data: { phone: DOCTOR_PHONE } });
  const code = (await req.json()).debug_code as string;
  const ver = await request.post(`${API}/auth/otp/verify`, { data: { phone: DOCTOR_PHONE, code } });
  cachedAccessToken = (await ver.json()).access_token as string;
  return cachedAccessToken;
}

async function openConsole(
  page: import("@playwright/test").Page,
  token: string,
  row: number,
): Promise<void> {
  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/doctor");
  await expect(page.locator(".appbar strong")).toHaveText("Dr. Anil Gupta");
  // These row indices are positions in the department's queue order, so this
  // suite reads the `Department` scope rather than Session B's `Mine` default —
  // the demo deliberately leaves one arrival in the pool, and `Mine` would shift
  // every index below it.
  await page.getByTestId("scope-department").click();
  const station = page.locator(".station").nth(row);
  await expect(station).toBeVisible();
  // The rail renders "Name · 58y" with the age in an <em>; the spine's heading is
  // the name alone, so compare against the name text node only.
  const name = await station
    .locator(".sname")
    .evaluate((el) => (el.childNodes[0]?.textContent ?? "").trim());
  await station.locator(".srow").click();
  // Wait for the stage to be showing *this* patient rather than whoever the day
  // fetch auto-opened — otherwise a test can silently dictate onto the wrong
  // visit, which is the one mistake this whole session is about.
  await expect(page.locator('[data-testid="context-spine"] h1')).toHaveText(name);
}

/** Open the note for the patient currently on the stage, typed not spoken. */
async function dictate(
  page: import("@playwright/test").Page,
  note: string,
  { alreadyOpen = false } = {},
) {
  if (!alreadyOpen) await page.keyboard.press("d");
  await expect(page.locator(".dict h2")).toHaveText("Consult note");
  await page.fill(".dict-transcript", note);
  await page.click(".dict-map");
  await expect(page.locator(".med").first()).toBeVisible({ timeout: 15_000 });
}

test("D opens the consult note for the patient on the stage", async ({ page, request }) => {
  await openConsole(page, await loginToken(request), ROW.opens);

  await page.keyboard.press("d");

  await expect(page.locator(".dict")).toBeVisible();
  // The note is a tab now, so the read tabs step aside — but the context spine
  // stays mounted above it (Session B). Losing the red flags at the moment a
  // prescription is being composed was the whole reason for the spine.
  await expect(page.locator('[data-testid="patient-card"]')).toHaveCount(0);
  await expect(page.getByTestId("context-spine")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-capture.png`, fullPage: true });

  // And D closes it again — the same key, not a second one to remember.
  await page.keyboard.press("d");
  await expect(page.locator('[data-testid="patient-card"]')).toBeVisible();
});

test("a dictation maps to fields, and every value shows the words it came from", async ({
  page,
  request,
}) => {
  await openConsole(page, await loginToken(request), ROW.maps);
  await dictate(page, NOTE);

  // Every drug carries its provenance line: what was written, and under it what
  // was said. This is the whole review.
  const spoken = await page.locator(".med-heard").allTextContents();
  expect(spoken.length).toBeGreaterThanOrEqual(3);
  expect(spoken.some((s) => s.includes("Monocef"))).toBe(true);

  await expect(page.locator(".prov-label").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/02-review.png`, fullPage: true });
});

test("an off-formulary drug is flagged, keeps its name, and blocks the signature", async ({
  page,
  request,
}) => {
  await openConsole(page, await loginToken(request), ROW.flags);
  await dictate(page, NOTE);

  const flagged = page.locator(".med-flag");
  await expect(flagged).toHaveCount(1);

  // The name is the doctor's, not the formulary's nearest neighbour.
  await expect(flagged.locator(".med-name")).toHaveText("Inj Ipilimumab 3 mg/kg");
  await expect(flagged.locator(".med-alert")).toContainText("Not on the hospital formulary");
  await expect(flagged.locator(".med-alert")).toContainText("Nothing has been changed for you");

  // And the signature is refused, by name, until it is confirmed.
  const sign = page.locator(".dict-sign");
  await expect(sign).toBeDisabled();
  await expect(page.locator(".dict-block")).toContainText("Inj Ipilimumab 3 mg/kg");
  await page.screenshot({ path: `${SHOTS}/03-flagged.png`, fullPage: true });
});

test("acknowledging the flag unlocks the signature without resolving the flag", async ({
  page,
  request,
}) => {
  await openConsole(page, await loginToken(request), ROW.acknowledges);
  await dictate(page, NOTE);

  await page.click(".med-confirm");

  await expect(page.locator(".med-acked")).toContainText("Still off-formulary");
  await expect(page.locator(".med-flag")).toHaveCount(0);
  await expect(page.locator(".med-ack")).toHaveCount(1); // calmed to marigold, not cleared
  await expect(page.locator(".dict-sign")).toBeEnabled();
  await page.screenshot({ path: `${SHOTS}/04-acknowledged.png`, fullPage: true });
});

test("signing locks the note", async ({ page, request }) => {
  await openConsole(page, await loginToken(request), ROW.signs);
  await page.keyboard.press("d");
  // Signing is terminal by design, so this one test cannot repeat on the same
  // row. Say so plainly rather than timing out on a disabled button sixty
  // seconds later — the fix is always `python -m scripts.seed_doctor_demo`.
  await expect(
    page.locator(".dict-signed"),
    "this row is already signed from an earlier run — re-seed the demo first",
  ).toHaveCount(0);
  await dictate(page, NOTE, { alreadyOpen: true });
  await page.click(".med-confirm");
  await expect(page.locator(".dict-sign")).toBeEnabled();

  await page.click(".dict-sign");

  await expect(page.locator(".dict-signed")).toContainText("This note is locked");
  await expect(page.locator(".dict-sign")).toHaveCount(0);
  await expect(page.locator(".dict-capture")).toHaveCount(0); // no re-dictating
  await expect(page.locator(".med-name").first()).toBeDisabled(); // no tap-to-fix
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${SHOTS}/05-signed.png`, fullPage: true });

  // Reopening shows the signed note, still locked — the lock is the record's,
  // not this component's local state.
  await page.reload();
  await page.getByTestId("scope-department").click();
  await page.locator(".station").nth(ROW.signs).locator(".srow").click();
  await page.keyboard.press("d");
  await expect(page.locator(".dict-signed")).toBeVisible();
});

// -- Session C: the paths that do not go through a model ----------------------

test("a prescription is produced with no speech at all", async ({ page, request }) => {
  // Session C's acceptance criterion. "Type note" opens the same record with no
  // model in the loop, and everything downstream — the formulary verdict, the
  // signature, the prescription — is the path a dictated note takes.
  //
  // It works the row the first test only *opened*, because signing is terminal
  // and every other row is spoken for. Re-seed (`python -m
  // scripts.seed_doctor_demo`) before a second run of this file.
  await openConsole(page, await loginToken(request), ROW.opens);
  await page.keyboard.press("d");
  await expect(page.locator(".dict h2")).toHaveText("Consult note");
  await expect(
    page.locator(".dict-signed"),
    "this row is already signed from an earlier run — re-seed the demo first",
  ).toHaveCount(0);

  // The four steps are stated, and the doctor is at the first of them.
  await expect(page.getByTestId("step-capture")).toHaveClass(/is-now/);

  await page.getByTestId("type-note").click();

  // No transcript, no model, and the fields are open anyway.
  await expect(page.getByTestId("step-review")).toHaveClass(/is-now/);
  await expect(page.locator(".dict-nomeds")).toBeVisible();

  await page.getByTestId("add-med").click();
  await page.getByTestId("add-med-name").fill("Tab Augmentin 625");
  await page.getByLabel("Frequency").first().fill("BD");
  await page.getByLabel("Duration").first().fill("5 days");
  await page.getByTestId("add-med-save").click();
  await expect(page.locator(".med-name").first()).toHaveText("Tab Augmentin 625");

  await page.getByLabel("Impression").fill("Acute tonsillitis");
  await page.locator(".dict-review").click(); // blur commits the field

  // Print is unreachable before approval: there is no prescription to print,
  // and the sign bar says so rather than showing a disabled button.
  await expect(page.getByTestId("print-patient")).toHaveCount(0);
  await expect(page.getByTestId("pre-print-note")).toContainText("produced by the signature");
  await page.screenshot({ path: `${SHOTS}/06-typed-note.png`, fullPage: true });

  await expect(page.getByTestId("sign-note")).toBeEnabled();
  await page.getByTestId("sign-note").click();

  await expect(page.getByTestId("prescription-issued")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".rx-name").first()).toContainText("Tab Augmentin 625");
  await expect(page.getByTestId("print-patient")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${SHOTS}/07-typed-prescription.png`, fullPage: true });
});

test("a mapping failure keeps the transcript instead of eating it", async ({ page, request }) => {
  // The server-side half of this — the fields opening on a recorded failure, and
  // that state reaching a signature — is `test_dictation.py`'s
  // `test_a_failed_mapping_still_reaches_a_signature_and_a_prescription`; a live
  // stack with a working fake model cannot be made to fail honestly from here.
  // What this proves is the part only a browser can: the doctor's words are
  // still on screen after the request fails.
  await openConsole(page, await loginToken(request), ROW.maps);
  await page.route("**/dictation/*/map", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "the mapping model is unreachable" }),
    }),
  );

  await page.keyboard.press("d");
  await page.fill(".dict-transcript", NOTE);
  await page.click(".dict-map");

  await expect(page.locator(".dict-err")).toContainText("unreachable");
  await expect(page.locator(".dict-transcript")).toHaveValue(NOTE);
});

test("a signed note cannot be re-dictated, even over the API", async ({ request }) => {
  const token = await loginToken(request);
  const day = await request.get(`${API}/doctor/day?scope=department`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  // The row the previous test signed.
  const visitId = (await day.json()).rows[ROW.signs].visit_id as string;

  const resp = await request.post(`${API}/dictation/visits/${visitId}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { transcript: "completely different note" },
  });

  expect(resp.status()).toBe(409);
});
