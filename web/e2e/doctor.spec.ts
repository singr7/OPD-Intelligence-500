// S9 doctor console (doc 03 §4/§5), driven against a live stack.
//
// This is the session's acceptance criterion as a test: a doctor completes a
// full morning on seed data — signs in, reads the urgent patient's card, calls
// the next token, sends one to the lab, marks a no-show, and finishes a
// consult — with every action going through the S8 queue verbs. Also captures
// the screenshots for the doc 04 §5 self-critique.
//
//   cd backend && .venv/bin/python -m scripts.seed_doctor_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=doctor

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/s9";
const DOCTOR_PHONE = "+915550001001"; // seeded Dr. Anil Gupta (MEDONC)

test.describe.configure({ mode: "serial" });

async function loginToken(request: import("@playwright/test").APIRequestContext): Promise<string> {
  const req = await request.post(`${API}/auth/otp/request`, { data: { phone: DOCTOR_PHONE } });
  const code = (await req.json()).debug_code as string;
  const ver = await request.post(`${API}/auth/otp/verify`, {
    data: { phone: DOCTOR_PHONE, code },
  });
  return (await ver.json()).access_token as string;
}

async function signedIn(page: import("@playwright/test").Page, token: string) {
  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/doctor");
  await expect(page.locator(".appbar strong")).toHaveText("Dr. Anil Gupta");
  await expect(page.locator(".station").first()).toBeVisible();
}

test("the doctor signs in with a phone OTP", async ({ page }) => {
  await page.goto("/doctor");
  await expect(page.locator(".login h1")).toHaveText("Sign in");
  await page.screenshot({ path: `${SHOTS}/01-login.png` });

  await page.click("button[type=submit]"); // Send code
  const hint = await page.locator(".hint").textContent();
  await page.fill("#code", (hint ?? "").replace(/\D/g, ""));
  await page.click("button[type=submit]"); // Sign in

  await expect(page.locator(".appbar strong")).toHaveText("Dr. Anil Gupta");
  await expect(page.locator(".appbar .room")).toHaveText("Medical Oncology");
});

test("the day rail lists the morning, urgent first, and opens the patient in the room", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  // The queue's own order: the febrile-neutropenia walk-in is at the top
  // because the rule fired, not because the console re-sorted it.
  const tokens = await page.locator(".station .stok").allTextContents();
  expect(tokens[0]).toBe("12");
  await expect(page.locator(".station").first()).toHaveClass(/urgent/);
  await expect(page.locator(".station").first()).toHaveClass(/is-active/);

  // The card opened on whoever is already in the room.
  await expect(page.locator("[data-testid=patient-card] .who h1")).toHaveText("Kamla Devi");
  await page.screenshot({ path: `${SHOTS}/02-day-and-card.png`, fullPage: true });
});

test("the red-flag strip leads the card, above the concern", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));

  const strip = page.locator("[data-testid=red-flag-strip]");
  await expect(strip).toBeVisible();
  await expect(strip.locator(".stamp").first()).toContainText(
    "Fever 38°C+ within 14 days of chemotherapy",
  );
  // The rule's own instruction rides along — the strip is not a bare label.
  await expect(strip.locator(".stamp").first()).toContainText("nurse");

  // It is physically above the chief concern: the 20-second read starts here.
  const stripBox = await strip.boundingBox();
  const concernBox = await page.locator(".concern").boundingBox();
  expect(stripBox!.y).toBeLessThan(concernBox!.y);

  await page.screenshot({ path: `${SHOTS}/03-red-flag-strip.png` });
});

test("the card carries the doc 03 §4 contract: symptoms, quote, trend, answers", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  await expect(page.locator(".symptoms tbody tr")).toHaveCount(5);
  await expect(page.locator(".symptoms tbody tr").first()).toContainText("Fever");
  await expect(page.locator(".own-words")).toContainText("घबराहट");

  // Everything else is collapsed until asked for (doc 04 §3).
  await expect(page.locator(".answers")).toHaveCount(0);
  await page.click(".fold-h:has-text('Intake answers')");
  await expect(page.locator(".answers li")).toHaveCount(12);
  await expect(page.locator(".answers li.flagged")).not.toHaveCount(0);

  await page.click(".fold-h:has-text('Check-in trend')");
  await expect(page.locator(".trends .spark")).toHaveCount(2);
  await page.screenshot({ path: `${SHOTS}/04-card-expanded.png`, fullPage: true });
});

test("N calls the next patient, and the rail follows", async ({ page, request }) => {
  const token = await loginToken(request);
  await signedIn(page, token);

  // Finish the patient in the room so there is a next one to call.
  await page.click(".act:has-text('Done')");
  await expect(page.locator(".station .stok").first()).not.toHaveText("12");

  await page.keyboard.press("n");
  await expect(page.locator(".station.is-active")).toHaveCount(1);
  const called = page.locator(".station.is-active .sname");
  await expect(called).toContainText("Ramesh Chand");
  await page.screenshot({ path: `${SHOTS}/05-called-next.png`, fullPage: true });
});

test("D shows that dictation is still S10 rather than pretending", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));
  await page.keyboard.press("d");
  await expect(page.locator(".note-toast")).toContainText("S10");
});

test("a full morning: lab re-queue, no-show, and consults completed", async ({ page, request }) => {
  const token = await loginToken(request);
  await signedIn(page, token);

  // Send the patient in the room to the lab: they leave the front and rejoin
  // at the back of their priority (the S8 queue verb, not a console rule).
  const firstName = await page.locator(".station").first().locator(".sname").textContent();
  await page.click(".act:has-text('Send to lab')");
  await expect(page.locator(".station").first().locator(".sname")).not.toHaveText(firstName!);

  // Work the waiting line down, following the S8 state machine rather than
  // wishing at it: a called patient goes called → in_consult → done, and a
  // no-show is only legal straight off `called`.
  for (let i = 0; i < 8; i += 1) {
    if ((await page.locator(".station.waiting").count()) === 0) break;
    await page.keyboard.press("n");
    await expect(page.locator(".station.called, .station.in_consult")).not.toHaveCount(0);
    if (i === 1) {
      await page.click(".act:has-text('No-show')"); // legal from `called`
    } else {
      await page.click(".act:has-text('Start consult')");
      await expect(page.locator(".station.in_consult")).not.toHaveCount(0);
      await page.click(".act:has-text('Done')");
    }
    await expect(page.locator(".station.called, .station.in_consult")).toHaveCount(0);
  }

  // The lab round-trip is still on the list — that is the point of it. Finish
  // them from where they are (lab_requeue → done is the legal exit).
  const atLab = page.locator(".station.lab_requeue");
  while ((await atLab.count()) > 0) {
    await atLab.first().locator("button").click();
    await page.click(".act:has-text('Done')");
    await page.waitForTimeout(200);
  }

  await expect(page.locator(".rail-empty")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/06-morning-cleared.png`, fullPage: true });
});
