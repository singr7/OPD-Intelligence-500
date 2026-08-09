// AR3 — arrival identity, the kiosk staff strip, and the desk's assign control,
// driven against a live stack. This file *is* the session's acceptance criterion.
//
// The four things it proves, in the order they matter:
//
//   1. A returning patient's phone number is recognised and the kiosk says
//      **nothing** about it — no name, no MRN, no "we found you". The
//      acknowledgement it does show is the same one a first-time patient who
//      typed a number sees, which is what stops the terminal being an oracle.
//   2. The staff strip is locked on arrival at the token screen, and the
//      candidate's name is nowhere in the DOM until a PIN is accepted.
//   3. A coordinator settles identity and doctor in one `Confirm`.
//   4. The desk can do the same thing for an arrival the strip skipped, which
//      is what makes `Skip` and the offline path safe.
//
//   cd backend && DATABASE_URL=... .venv/bin/python -m app.seed   # PIN holder
//   API_BASE=http://127.0.0.1:8000 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=assign
//
// ⚠️ It issues real tokens on whatever database it points at. Fine on a dev box.

import { expect, test, type Page } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8000";
const SHOTS = "screenshots/ar3";

// Seeded by `app.seed`: patient OPD000001 carries +915551900001, and Rekha Meena
// is the coordinator holding the seeded kiosk PIN (seeds/doctors.json).
const RETURNING_PHONE = "5551900001";
const PIN_HOLDER = "Rekha Meena";
const PIN = "4729";
const COORDINATOR_PHONE = "+915550000002";

async function shot(page: Page, name: string) {
  await page.waitForTimeout(350);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

async function typeInto(page: Page, text: string) {
  const toggle = page.getByTestId("type-toggle");
  if (await toggle.count()) await toggle.click();
  await page.getByRole("textbox").fill(text);
}

/** Submit answers only once the button says it will take one. The kiosk disables
 *  it while a save is in flight, so a bare click races the previous question. */
async function submitAnswer(page: Page) {
  const button = page.getByTestId("answer-submit");
  await expect(button).toBeEnabled({ timeout: 15_000 });
  await button.click();
}

/** Walk an intake to the token screen. `phone` null = a first-time arrival.
 *
 *  The patient name carries a run id: this suite issues real tokens against a
 *  dev database that keeps them, and a fixed name would have the console test
 *  find yesterday's already-assigned row instead of the one it just made. */
async function walkToToken(page: Page, opts: { phone: string | null; name?: string }) {
  await page.goto("/kiosk");
  await page.getByTestId("welcome-lang-en").click();
  await page.getByTestId("caregiver-self").click();

  if (opts.phone) {
    await page.getByTestId("returning-yes").click();
    for (const digit of opts.phone) await page.getByTestId(`arrival-phone-${digit}`).click();
    await page.getByTestId("arrival-phone-next").click();
    await page.getByTestId("arrival-id-skip").click();
  } else {
    await page.getByTestId("returning-no").click();
  }

  await expect(page.locator("main")).toHaveAttribute("data-screen", "details");
  await page.getByTestId("patient-name").fill(opts.name ?? "Test Arrival");
  await page.getByTestId("patient-age").fill("52");
  await page.getByTestId("patient-sex-female").click();
  await page.getByTestId("details-next").click();

  await typeInto(page, "mujhe pet mein dard hai");
  await page.getByTestId("cc-next").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "chooser", {
    timeout: 20_000,
  });
  await page.getByTestId("option").filter({ hasText: "Medical Oncology" }).click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "question", {
    timeout: 20_000,
  });

  for (let i = 0; i < 40; i++) {
    if ((await page.getAttribute("main", "data-screen")) !== "question") break;
    const type = await page.getAttribute("main", "data-node-type");
    if (type === "single") {
      await page.getByTestId("option").first().click();
    } else if (type === "multi" || type === "body_map") {
      await page.getByTestId("option").first().click();
      await submitAnswer(page);
    } else if (type === "scale") {
      await page.getByTestId("face").nth(3).click();
      await submitAnswer(page);
    } else if (type === "number") {
      await submitAnswer(page);
    } else if (type === "free_voice") {
      await typeInto(page, "theek hai");
      await submitAnswer(page);
    }
    await page.waitForTimeout(400);
  }

  // The allergy question (SESSION-ALLERGY) sits between the tree and the
  // read-back now, and it is asked of every intake in every department. These
  // walks tap "I don't know", which records nothing — the fastest way past a
  // screen this suite is not about.
  await expect(page.locator("main")).toHaveAttribute("data-screen", "allergy", {
    timeout: 20_000,
  });
  await page.getByTestId("allergy-unsure").click();

  await expect(page.locator("main")).toHaveAttribute("data-screen", "readback", {
    timeout: 20_000,
  });
  await page.getByTestId("confirm").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "token", {
    timeout: 20_000,
  });
}

async function unlockStrip(page: Page) {
  await page.getByTestId("staff-unlock").click();
  await page.getByTestId("staff-holder").filter({ hasText: PIN_HOLDER }).click();
  for (const digit of PIN) await page.getByTestId(`staff-pin-${digit}`).click();
  await page.getByTestId("staff-pin-submit").click();
  await expect(page.getByTestId("staff-open")).toBeVisible({ timeout: 15_000 });
}

test("a returning arrival is recognised without the kiosk disclosing anything", async ({
  page,
}) => {
  await walkToToken(page, { phone: RETURNING_PHONE });

  // The strip is locked, and the match is invisible — this is the assertion the
  // whole design exists for. `test_kiosk_strip.py` proves the server half.
  const strip = page.getByTestId("staff-strip");
  await expect(strip).toHaveAttribute("data-phase", "locked");
  await expect(page.getByTestId("staff-candidate")).toHaveCount(0);
  const body = await page.locator("body").innerText();
  expect(body).not.toContain("OPD000001");
  expect(body.toLowerCase()).not.toContain("mrn");
  await shot(page, "01-token-strip-locked");

  await unlockStrip(page);

  // Behind the PIN, and only there, the coordinator sees who this might be.
  await expect(page.getByTestId("staff-candidate")).toBeVisible();
  await expect(page.getByTestId("staff-candidate-name")).not.toBeEmpty();
  await shot(page, "02-strip-unlocked-candidate");

  // Identity and doctor settle in one action.
  await page.getByTestId("staff-link-yes").click();
  await page.getByTestId("staff-confirm").click();
  await expect(strip).toHaveAttribute("data-phase", "locked", { timeout: 15_000 });
  // Relocking is not cosmetic: the candidate is gone from the DOM again.
  await expect(page.getByTestId("staff-candidate")).toHaveCount(0);
});

test("a first-time arrival gets no candidate, and the strip says so plainly", async ({ page }) => {
  await walkToToken(page, { phone: null });
  await unlockStrip(page);
  await expect(page.getByTestId("staff-no-candidate")).toBeVisible();
  await expect(page.getByTestId("staff-candidate")).toHaveCount(0);
  await shot(page, "03-strip-no-candidate");
});

test("the desk can assign an arrival the kiosk skipped", async ({ page, request }) => {
  const name = `Skipped Arrival ${Date.now()}`;
  await walkToToken(page, { phone: null, name });
  await unlockStrip(page);
  // Skip is a first-class outcome: the visit lands in the department pool.
  await page.getByTestId("staff-skip").click();
  await expect(page.getByTestId("staff-strip")).toHaveAttribute("data-phase", "locked", {
    timeout: 15_000,
  });

  // Now the coordinator's console, which is the compensating control.
  const otp = await request.post(`${API}/auth/otp/request`, {
    data: { phone: COORDINATOR_PHONE },
  });
  const code = (await otp.json()).debug_code as string;
  const verified = await request.post(`${API}/auth/otp/verify`, {
    data: { phone: COORDINATOR_PHONE, code },
  });
  const token = (await verified.json()).access_token as string;

  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/coordinator");
  await expect(page.locator(".queue-page")).toBeVisible({ timeout: 20_000 });

  // The unassigned state is stated on the row, not left as a blank.
  const row = page.locator(".entry", { hasText: name }).first();
  await expect(row.getByTestId("entry-unassigned")).toBeVisible();
  await shot(page, "04-console-unassigned");

  await row.getByTestId("assign-open").click();
  await expect(page.getByTestId("assign-panel")).toBeVisible();
  await page.getByTestId("assign-panel").scrollIntoViewIfNeeded();
  await shot(page, "05-console-assign-panel");

  const doctor = page.getByTestId("assign-doctor");
  await doctor.selectOption({ index: 1 });
  await page.getByTestId("assign-save").click();

  await expect(row.getByTestId("entry-doctor")).toBeVisible({ timeout: 20_000 });
  await shot(page, "06-console-assigned");
});
