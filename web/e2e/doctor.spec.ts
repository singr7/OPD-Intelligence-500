// The doctor console (doc 03 §4/§5, plan §3/§4), driven against a live stack.
//
// This is the session's acceptance criterion as a test. Two mornings, really:
//
//   * S9's — a doctor signs in, reads the urgent patient, calls the next token,
//     sends one to the lab, marks a no-show and finishes the line.
//   * Session B's — the worklist is scoped, the unassigned arrival is impossible
//     to miss, one tap takes her, and the context spine stays on screen through
//     every tab including the consult note.
//
// Also captures the screenshots for the doc 04 §5 self-critique.
//
//   cd backend && .venv/bin/python -m scripts.seed_doctor_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=doctor

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/s9";
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

/**
 * Finish the consult on the stage.
 *
 * Completing is a *conclusion* now, not a bare queue transition (plan §5.3b): a
 * visit that simply stops cannot be told apart from one the doctor was
 * interrupted in the middle of. With a signed note it stays one tap; without
 * one the dialog opens and has to be answered, which is the point of it.
 */
async function completeConsult(
  page: import("@playwright/test").Page,
  mode: "external_manual" | "none" = "none",
) {
  await page.click("[data-testid='complete-consult']");
  const dialog = page.getByTestId("conclude-dialog");
  await dialog.waitFor({ state: "visible", timeout: 2000 }).catch(() => {});
  if (await dialog.count()) {
    await page.getByTestId(`rx-mode-${mode}`).click();
    await page.getByTestId("conclude-confirm").click();
    await expect(dialog).toHaveCount(0);
  }
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

  await page.fill("#phone", DOCTOR_PHONE);
  await page.click("button[type=submit]"); // Send code
  const hint = await page.getByTestId("otp-hint").textContent();
  await page.fill("#code", (hint ?? "").replace(/\D/g, ""));
  await page.click("button[type=submit]"); // Sign in

  await expect(page.locator(".appbar strong")).toHaveText("Dr. Anil Gupta");
  // The room line carries the date too since S-UX.6 — a console left open
  // overnight should not silently show yesterday's list as today's.
  await expect(page.locator(".appbar .room")).toContainText("Medical Oncology");
  cachedAccessToken = await page.evaluate(() => localStorage.getItem("opd_staff_token"));
});

test("the day rail lists the morning, urgent first, and opens the patient in the room", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  // The default scope is `Mine`.
  await expect(page.getByTestId("scope-mine")).toHaveAttribute("aria-selected", "true");

  // The queue's own order: the febrile-neutropenia walk-in is at the top
  // because the rule fired, not because the console re-sorted it.
  const tokens = await page.locator(".station .stok").allTextContents();
  expect(tokens[0]).toBe("12");
  await expect(page.locator(".station").first()).toHaveClass(/urgent/);
  await expect(page.locator(".station").first()).toHaveClass(/is-active/);

  // The card opened on whoever is already in the room.
  await expect(page.locator("[data-testid=context-spine] h1")).toHaveText("Kamla Devi");
  await page.screenshot({ path: `${SHOTS}/02-day-and-card.png`, fullPage: true });
});

test("the unassigned arrival is impossible to miss from the Mine tab", async ({ page, request }) => {
  // Session B's whole safety net. The seed leaves Sita Kumari in the department
  // pool the way an offline kiosk does — with no roster to pick from, nobody's
  // name is on her. She is not in `Mine`, and the console still has to say so.
  await signedIn(page, await loginToken(request));

  await expect(page.getByTestId("scope-mine")).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".station .sname")).not.toContainText(["Sita Kumari"]);

  // The count is visible while its tab is closed, and it is an attention state
  // stated in words, not colour alone.
  await expect(page.getByTestId("count-unassigned")).toHaveText("1");
  await expect(page.getByTestId("unassigned-alert")).toContainText("1 waiting with no doctor");
  await page.screenshot({ path: `${SHOTS}/07-unassigned-badge.png` });
});

test("Take this patient moves her onto the doctor's own list", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));

  await page.getByTestId("scope-unassigned").click();
  await expect(page.locator(".station")).toHaveCount(1);
  await expect(page.locator(".station .sname")).toContainText("Sita Kumari");
  await expect(page.locator(".station .swho")).toHaveText("No doctor assigned");
  await page.screenshot({ path: `${SHOTS}/08-unassigned-scope.png`, fullPage: true });

  await page.locator(".station .take").click();

  // Taking her opens her: the doctor said "I'll see this one".
  await expect(page.locator("[data-testid=context-spine] h1")).toHaveText("Sita Kumari");
  // The pool is empty and the attention state is gone — no stale alarm.
  await expect(page.getByTestId("count-unassigned")).toHaveText("0");
  await expect(page.getByTestId("unassigned-alert")).toHaveCount(0);

  await page.getByTestId("scope-mine").click();
  await expect(page.locator(".station .sname")).toContainText(["Sita Kumari"]);
});

test("the department scope names a colleague's patient rather than leaving it blank", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));
  await page.getByTestId("scope-department").click();

  const counts = page.getByTestId("count-department");
  await expect(counts).not.toHaveText("0");
  // Every row in the department list is either this doctor's or labelled. An
  // unlabelled row would be indistinguishable from an unassigned one.
  const rows = page.locator(".station");
  for (let i = 0; i < (await rows.count()); i += 1) {
    const row = rows.nth(i);
    const labelled = await row.locator(".swho").count();
    const takeable = await row.locator(".take").count();
    expect(labelled === takeable).toBe(true);
  }
});

test("the context spine leads the stage and survives every tab", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));

  const spine = page.getByTestId("context-spine");
  await expect(spine).toBeVisible();

  // Four things and no fifth: identity + token, diagnosis, allergies, red flags.
  await expect(spine.locator(".cx-tok-n")).toHaveText("12");
  await expect(page.getByTestId("spine-diagnosis")).toBeVisible();
  // SESSION-ALLERGY: the seeded patient has never been asked, and the line says
  // exactly that rather than going quiet or claiming there are none.
  await expect(page.getByTestId("spine-allergies")).toContainText("ask the patient");
  await expect(page.getByTestId("spine-allergies")).not.toContainText("no known");
  const strip = page.getByTestId("red-flag-strip");
  await expect(strip.locator(".stamp").first()).toContainText(
    "Fever 38°C+ within 14 days of chemotherapy",
  );
  // The rule's own instruction rides along — the strip is not a bare label.
  await expect(strip.locator(".stamp").first()).toContainText("nurse");

  // It is physically above the tab row: the 20-second read starts here.
  const spineBox = await spine.boundingBox();
  const tabsBox = await page.locator(".worktabs").boundingBox();
  expect(spineBox!.y).toBeLessThan(tabsBox!.y);

  // And it is still mounted on every one of the four tabs — including the
  // consult note, which used to replace the whole stage and take the red flags
  // with it exactly when the doctor was composing a prescription.
  for (const tab of ["answers", "history", "consult", "overview"]) {
    await page.getByTestId(`tab-${tab}`).click();
    await expect(page.getByTestId("context-spine")).toBeVisible();
    await expect(page.getByTestId("red-flag-strip")).toBeVisible();
  }
  await page.screenshot({ path: `${SHOTS}/03-context-spine.png` });
});

test("the tabs carry the doc 03 §4 contract, and provenance instead of a percentage", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  await expect(page.locator(".symptoms tbody tr")).toHaveCount(5);
  await expect(page.locator(".symptoms tbody tr").first()).toContainText("Fever");
  await expect(page.locator(".own-words")).toContainText("घबराहट");

  // No confidence percentage anywhere — four facts a doctor can weigh instead.
  const provenance = page.getByTestId("provenance");
  await expect(provenance).toContainText("Answered by");
  await expect(provenance).toContainText("Hindi");
  await expect(page.locator(".work")).not.toContainText("% confidence");

  await page.getByTestId("tab-answers").click();
  await expect(page.locator(".answers li")).toHaveCount(12);
  await expect(page.locator(".answers li.flagged")).not.toHaveCount(0);

  await page.getByTestId("tab-history").click();
  await expect(page.getByTestId("history-allergies")).toContainText("Nobody has asked");
  await expect(page.locator(".trends .spark")).toHaveCount(2);
  await page.screenshot({ path: `${SHOTS}/04-tabs.png`, fullPage: true });
});

test("Coming soon is one quiet entry with no mock clinical content", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));

  // Six tabs now, not eight — and the count is asserted because the whole point
  // of the disclosure is that unbuilt surfaces are *not* among them.
  //
  // This assertion said four until M3, and had been wrong since MRD2 graduated
  // Reports: nobody ran this project for three sessions, so a stale number sat
  // green in nobody's terminal. Overview, Intake answers, History, Reports,
  // Research, Consult.
  await expect(page.locator(".wtab")).toHaveCount(6);
  await page.getByTestId("coming-soon").click();

  const panel = page.getByTestId("coming-soon-panel");
  // Imaging left this list in M3 when it shipped — as a section of Reports
  // rather than a seventh tab. A feature that is live must not still be
  // advertised as upcoming; that teaches doctors not to look for it.
  await expect(panel).not.toContainText("Imaging");
  await expect(panel).toContainText("NCCN Guidelines");
  // Lab reports is the one a doctor could read as broken rather than absent —
  // this system already has a lab_requeue state, so it says so in words.
  await expect(panel).toContainText("Not live yet");
  await page.screenshot({ path: `${SHOTS}/09-coming-soon.png` });
});

test("N calls the next patient, and the rail follows", async ({ page, request }) => {
  const token = await loginToken(request);
  await signedIn(page, token);

  // Finish the patient in the room so there is a next one to call.
  await completeConsult(page);
  await expect(page.locator(".station .stok").first()).not.toHaveText("12");

  await page.keyboard.press("n");
  await expect(page.locator(".station.is-active")).toHaveCount(1);
  const called = page.locator(".station.is-active .sname");
  await expect(called).toContainText("Ramesh Chand");
  await page.screenshot({ path: `${SHOTS}/05-called-next.png`, fullPage: true });
});

test("a paper prescription is written down rather than left as a blank visit", async ({
  page,
  request,
}) => {
  // Plan §5.3b. The doctor who writes on the OPD pad has finished the consult;
  // until now that left a visit indistinguishable from an abandoned one.
  const token = await loginToken(request);
  await signedIn(page, token);

  const called = page.locator(".station.called, .station.in_consult");
  if ((await called.count()) === 0) await page.keyboard.press("n");
  await expect(called.first()).toBeVisible();
  const start = page.getByTestId("start-consult");
  if (await start.count()) await start.click();

  // Which visit this is, so the record can be read back afterwards.
  const day = await request.get(`${API}/doctor/day?scope=department`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const row = (await day.json()).rows.find(
    (r: { state: string }) => r.state === "in_consult" || r.state === "called",
  );
  expect(row).toBeTruthy();

  await page.click("[data-testid='complete-consult']");
  const dialog = page.getByTestId("conclude-dialog");
  await expect(dialog).toBeVisible();

  // The warning names what will not exist, rather than the prototype's vague
  // "won't capture this visit findings". Vague warnings get clicked through.
  await page.getByTestId("rx-mode-external_manual").click();
  await expect(dialog).toContainText("No consult note and no digital prescription");
  await expect(dialog).toContainText("The pharmacy will have no digital copy");
  await expect(dialog).toContainText("Follow-up reminders cannot be generated");
  await page.screenshot({ path: `${SHOTS}/10-conclude.png` });

  await dialog.locator("textarea").fill("Written on the OPD pad.");
  await page.getByTestId("conclude-confirm").click();
  await expect(dialog).toHaveCount(0);

  // The record says how it ended, and the queue entry is done.
  const card = await request.get(`${API}/doctor/patients/${row.visit_id}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await card.json();
  expect(body.rx_mode).toBe("external_manual");
  expect(body.conclusion_note).toBe("Written on the OPD pad.");
  expect(body.entry_state).toBe("done");
});

test("D opens the consult note for the patient on the stage", async ({ page, request }) => {
  // S10 shipped the real note. What this still guards is the S-UX.6 rule that
  // the note belongs to a visit — D does nothing until somebody is on the stage
  // — and Session B's addition: it is now a tab, so the spine does not unmount.
  await signedIn(page, await loginToken(request));
  await page.keyboard.press("d");
  await expect(page.locator(".dict h2")).toHaveText("Consult note");
  await expect(page.getByTestId("context-spine")).toBeVisible();
  await page.keyboard.press("d");
  await expect(page.locator(".dict")).toHaveCount(0);
});

test("a full morning: lab re-queue, no-show, and consults completed", async ({ page, request }) => {
  const token = await loginToken(request);
  await signedIn(page, token);

  // Work the whole department, not just this doctor's slice — the point of the
  // department scope is that one doctor can clear the room.
  await page.getByTestId("scope-department").click();

  // Send the patient in the room to the lab: they leave the front and rejoin
  // at the back of their priority (the S8 queue verb, not a console rule).
  // The room may be empty — the test before this one concludes whoever was in
  // it — so call someone in first rather than assuming the previous test's
  // leftovers.
  if ((await page.locator(".station.called, .station.in_consult").count()) === 0) {
    await page.keyboard.press("n");
    await expect(page.locator(".station.called, .station.in_consult")).not.toHaveCount(0);
  }
  const firstName = await page.locator(".station").first().locator(".sname").textContent();
  // Lab re-queue is only legal from `in_consult`, so start the consult first —
  // the encounter bar offers exactly the transition the state machine allows.
  const start = page.getByTestId("start-consult");
  if (await start.count()) await start.click();
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
      await page.click("[data-testid='start-consult']");
      await expect(page.locator(".station.in_consult")).not.toHaveCount(0);
      await completeConsult(page);
    }
    await expect(page.locator(".station.called, .station.in_consult")).toHaveCount(0);
  }

  // The lab round-trip is still on the list — that is the point of it. Finish
  // them from where they are (lab_requeue → done is the legal exit).
  const atLab = page.locator(".station.lab_requeue");
  while ((await atLab.count()) > 0) {
    await atLab.first().locator("button").first().click();
    await completeConsult(page);
    await page.waitForTimeout(200);
  }

  await expect(page.locator(".rail-empty")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/06-morning-cleared.png`, fullPage: true });
});
