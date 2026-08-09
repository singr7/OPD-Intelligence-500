// Allergy capture (SESSION-ALLERGY), driven against a live stack.
//
// This project *is* the session's acceptance criterion, and the criterion is a
// distinction rather than a feature: three states that must never collapse into
// each other, on a screen a doctor prescribes from.
//
// The morning it walks:
//
//   1. the doctor opens a patient nobody has asked, and the spine says so —
//      not "no known allergies", which is the sentence this product has refused
//      to print since Session B;
//   2. the doctor records penicillin, severe, and the line turns and names it;
//   3. the doctor withdraws it, and it is still on file, struck out, with a
//      reason and a name on it;
//   4. the doctor records "I asked — she reports none", and that reads as an
//      answer somebody gave rather than as a fact the record established.
//
//   cd backend && .venv/bin/python -m scripts.seed_doctor_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=allergy

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/allergy";
const DOCTOR_PHONE = "+915550001001"; // seeded Dr. Anil Gupta (MEDONC)
let cachedAccessToken: string | null = null;

test.describe.configure({ mode: "serial" });

async function loginToken(request: import("@playwright/test").APIRequestContext): Promise<string> {
  if (cachedAccessToken) return cachedAccessToken;
  const req = await request.post(`${API}/auth/otp/request`, { data: { phone: DOCTOR_PHONE } });
  const code = (await req.json()).debug_code as string;
  const ver = await request.post(`${API}/auth/otp/verify`, {
    data: { phone: DOCTOR_PHONE, code },
  });
  cachedAccessToken = (await ver.json()).access_token as string;
  return cachedAccessToken;
}

async function signedIn(page: import("@playwright/test").Page, token: string) {
  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/doctor");
  await expect(page.locator(".appbar strong")).toHaveText("Dr. Anil Gupta");
  await expect(page.locator(".station").first()).toBeVisible();
}

/** Open the panel from the spine's third slot. */
async function openPanel(page: import("@playwright/test").Page) {
  await page.getByTestId("spine-allergies").click();
  await expect(page.getByTestId("allergy-panel")).toBeVisible();
}

test("a patient nobody asked is stated as such, and never as 'no known allergies'", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  const line = page.getByTestId("spine-allergies");
  await expect(line).toBeVisible();
  await expect(line).toHaveAttribute("data-state", "never_asked");
  await expect(line).toContainText("ask the patient");

  // The sentence this module exists to refuse, checked over the whole console
  // rather than over the one line — it must not appear anywhere on the screen.
  await expect(page.locator("body")).not.toContainText(/no known allerg/i);

  // Quiet, not amber. Every patient starts here, so a coloured band would be on
  // every console all day — and it must never outshout a severe allergy, which
  // is exactly what the first cut of this did.
  await expect(line).toHaveAttribute("data-tone", "quiet");

  await page.screenshot({ path: `${SHOTS}/01-never-asked.png` });
});

test("the spine's slot is a control the doctor can act in without losing their tab", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  // Read something first — the panel must not cost the doctor this.
  await page.getByTestId("tab-answers").click();
  await expect(page.locator(".answers li").first()).toBeVisible();

  await openPanel(page);
  await expect(page.getByTestId("allergy-empty")).toContainText("Nobody has asked");
  await page.screenshot({ path: `${SHOTS}/02-panel-empty.png` });

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("allergy-panel")).toHaveCount(0);
  // Still on Answers, still scrolled to the same work.
  await expect(page.locator(".answers li").first()).toBeVisible();
});

test("a doctor records a severe allergy and the spine names it", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));
  await openPanel(page);

  await page.getByTestId("allergy-substance").fill("penicillin");
  await page.getByTestId("allergy-reaction").fill("throat closed");
  await page.getByTestId("allergy-sev-severe").click();
  await page.getByTestId("allergy-add").click();

  const item = page.getByTestId("allergy-item").first();
  await expect(item).toContainText("penicillin");
  await expect(item).toContainText("throat closed");
  // A doctor's own statement needs no second doctor to stand behind it.
  await expect(item).toContainText("confirmed");
  await page.screenshot({ path: `${SHOTS}/03-panel-known.png` });

  await page.keyboard.press("Escape");

  const line = page.getByTestId("spine-allergies");
  await expect(line).toHaveAttribute("data-state", "known");
  await expect(line).toHaveAttribute("data-tone", "danger");
  await expect(line).toContainText("penicillin");
  // The word rides with the colour, always.
  await expect(line).toContainText("severe");

  // The spine is read in two seconds and must not have grown a second line.
  const box = await line.boundingBox();
  expect(box!.height).toBeLessThan(56);

  await page.screenshot({ path: `${SHOTS}/04-spine-severe.png` });
});

test("the History tab carries the same statement in full, with its provenance", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  await page.getByTestId("tab-history").click();
  const history = page.getByTestId("history-allergies");
  await expect(history).toContainText("penicillin");
  await expect(history).toContainText("throat closed");
  // Who said it and when, on the same line as the fact it qualifies — a source
  // that scrolled out of view is a fact the doctor reads as established.
  await expect(history).toContainText("recorded by");
  await page.screenshot({ path: `${SHOTS}/05-history.png` });
});

test("withdrawing a statement leaves it on file, struck out, with a reason", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));
  await openPanel(page);

  await page.getByTestId("allergy-retract").first().click();
  await page.getByTestId("allergy-reason").fill("charted from the wrong file");
  await page.getByTestId("allergy-retract-confirm").click();

  // Gone from the current picture…
  await expect(page.getByTestId("allergy-item")).toHaveCount(0);
  // …and still readable, with who took it back and why.
  const gone = page.getByTestId("allergy-retracted");
  await expect(gone).toContainText("penicillin");
  await expect(gone).toContainText("charted from the wrong file");
  await expect(gone).toContainText("Anil Gupta");
  await page.screenshot({ path: `${SHOTS}/06-withdrawn.png` });

  await page.keyboard.press("Escape");

  // A record with nothing live on it is back to "ask", not to "none" — a
  // withdrawal is not reassurance.
  const line = page.getByTestId("spine-allergies");
  await expect(line).toHaveAttribute("data-state", "never_asked");
  await expect(line).toContainText("ask the patient");
});

test("'I asked and she reports none' is recorded as an answer, not as a conclusion", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));
  await openPanel(page);

  await page.getByTestId("allergy-none-known").click();
  await expect(page.getByTestId("allergy-none")).toContainText("None stated");

  await page.keyboard.press("Escape");

  const line = page.getByTestId("spine-allergies");
  await expect(line).toHaveAttribute("data-state", "none_stated");
  await expect(line).toContainText("none stated");
  // It never travels without its source and its date…
  await expect(line).toContainText("recorded by");
  // …and it is still not the sentence this record refuses to write.
  await expect(page.locator("body")).not.toContainText(/no known allerg/i);

  await page.screenshot({ path: `${SHOTS}/07-none-stated.png` });
});
