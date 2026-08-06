// M4 — the ambient consult note, driven against a live stack. This file *is*
// the session's frontend acceptance criterion.
//
// The five things it proves, in the order they matter:
//
//   1. **The mic is reachable from every tab and never unmounts.** That is the
//      module's structural claim — an observation is captured *while* reading,
//      so a recorder that lived inside a tab would not deliver it.
//   2. **The drawer does not take the screen away.** The context spine —
//      identity, diagnosis, allergies, red flags — is still visible while the
//      doctor reviews what they just said. Session B's rule, held here.
//   3. **The review is a review.** What they said sits beside what the machine
//      made of it, an edit sticks, and Confirm locks the note.
//   4. **A note cannot prescribe, and the screen says so.** There is no
//      medication field anywhere in the drawer, and the rule is on screen.
//   5. **A failed mapping keeps the words.** The observation is on the record,
//      the fields open empty, and the doctor can still confirm.
//
//   cd backend && .venv/bin/python -m scripts.seed_doctor_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=notes
//
// The LLM may stay `fake` — it has a canned `note_map` reply, so the real
// mapping path runs end to end with no vendor.
//
// **Why the recording itself is not driven from here.** Headless Chromium has
// no microphone and no Web Speech, so `getUserMedia` never resolves and the
// analyser never exists. Driving a fake one would prove that a fake works. The
// capture is therefore seeded through the same API the mic posts to, and this
// file proves the part only a browser can: that the surface renders, survives a
// tab change, keeps the spine, and refuses what it should. The level ring is in
// the known-gaps list for the same reason Session C's meter is — it needs a look
// on real hardware with a real headset.
//
// ⚠️ It writes real clinical notes on whatever database it points at. Dev boxes
// only.

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/m4";
const DOCTOR_PHONE = "+915550001001"; // seeded Dr. Anil Gupta (MEDONC)

const SPOKEN =
  "post-chemo cycle 3, tolerating well, grade 1 mucositis, review CBC before the next cycle";

test.describe.configure({ mode: "serial" });

async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

/** One token per phone, taken through the API. The OTP resend cooldown is 30
 *  seconds, so a 429 is waited out rather than reported as a broken suite. */
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

type Patient = { visitId: string; name: string; tokenNo: number };

async function firstPatient(request: APIRequestContext, doctor: string): Promise<Patient> {
  const day = await request.get(`${API}/doctor/day?scope=department`, {
    headers: { Authorization: `Bearer ${doctor}` },
  });
  const rows = (await day.json()).rows;
  expect(rows.length, "the seeded day has nobody on it — run seed_doctor_demo").toBeGreaterThan(0);
  return { visitId: rows[0].visit_id, name: rows[0].patient_name, tokenNo: rows[0].token_no };
}

/** Capture an observation the way the mic does: store the words, then map them.
 *  Two calls, because that split is what keeps the words when mapping fails. */
async function speak(
  request: APIRequestContext,
  doctor: string,
  visitId: string,
  transcript = SPOKEN,
  { map = true } = {},
): Promise<string> {
  const started = await request.post(`${API}/notes/visits/${visitId}`, {
    headers: { Authorization: `Bearer ${doctor}` },
    data: { transcript },
  });
  expect(started.status()).toBe(200);
  const noteId = (await started.json()).id;

  if (map) {
    const mapped = await request.post(`${API}/notes/${noteId}/map`, {
      headers: { Authorization: `Bearer ${doctor}` },
    });
    expect(
      mapped.status(),
      "the mapping failed — check the LLM profile on the api",
    ).toBe(200);
  }
  return noteId;
}

async function openConsole(page: Page, token: string, tokenNo?: number) {
  // The same key the coordinator console uses — both are staff logins against
  // the same /auth endpoints (see `_lib/session.ts`).
  // `domcontentloaded` rather than the default `load`: against a dev server the
  // load event waits on HMR's socket and never settles, which times out a test
  // that has nothing to do with what it is proving.
  await page.goto("/doctor", { waitUntil: "domcontentloaded" });
  await page.evaluate((t) => window.localStorage.setItem("opd_staff_token", t), token);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("context-spine")).toBeVisible({ timeout: 30_000 });
  if (tokenNo != null) {
    await page.getByTestId(`station-${tokenNo}`).click();
    await expect(page.getByTestId("context-spine")).toBeVisible();
  }
}

test.describe("the doctor thinks aloud", () => {
  let doctorToken = "";
  let patient: Patient;

  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    doctorToken = await tokenFor(request, DOCTOR_PHONE);
    patient = await firstPatient(request, doctorToken);
    await request.dispose();
  });

  test("the mic is on every tab and does not unmount when the tab changes", async ({ page }) => {
    await openConsole(page, doctorToken, patient.tokenNo);

    // The claim, tab by tab. Not a loop over three assertions for its own sake:
    // the Consult tab is the one that matters, because that is where the panel
    // used to replace the whole work area.
    const mic = page.getByTestId("note-mic");
    await expect(mic).toBeVisible();

    for (const tab of ["answers", "history", "reports", "consult"]) {
      await page.getByTestId(`tab-${tab}`).click();
      await expect(mic, `the mic vanished on the ${tab} tab`).toBeVisible();
    }

    await shot(page, "01-mic-on-consult-tab");
  });

  test("reviewing an observation leaves the spine on screen", async ({ page, playwright }) => {
    const request = await playwright.request.newContext();
    await speak(request, doctorToken, patient.visitId);
    await request.dispose();

    await openConsole(page, doctorToken, patient.tokenNo);

    // The dock counts what is waiting, and the count is the way in.
    await expect(page.getByTestId("note-drafts")).toBeVisible();
    await page.getByTestId("note-resume").click();

    const drawer = page.getByTestId("note-drawer");
    await expect(drawer).toBeVisible();

    // 2. The whole reason this is a drawer and not a tab — and it is checked by
    // geometry, not by `toBeVisible`.
    //
    // The first version of this test asserted `toBeVisible` on the spine and
    // passed while the drawer sat squarely on top of it: Playwright's
    // visibility means "in the DOM with a non-zero box", which a covered
    // element still has. The screenshot is what caught it. So the assertion is
    // now the thing actually claimed — the whole spine, down to the red flags,
    // sits above the top edge of the drawer.
    const spineBox = await page.getByTestId("context-spine").boundingBox();
    const drawerBox = await drawer.boundingBox();
    expect(spineBox, "no spine on screen").not.toBeNull();
    expect(drawerBox, "no drawer on screen").not.toBeNull();
    expect(
      spineBox!.y + spineBox!.height,
      "the drawer covered the spine — an observation is captured *while* reading",
    ).toBeLessThanOrEqual(drawerBox!.y + 1);
    expect(spineBox!.y, "the spine was scrolled off the top instead").toBeGreaterThanOrEqual(0);

    // And the parts of it that matter most are among what stayed.
    //
    // `count()` first, deliberately: `boundingBox()` on a locator that matches
    // nothing waits for the element with no timeout, so an absent id hangs the
    // test rather than failing it. Both red-flag ids are listed because exactly
    // one of them is ever rendered — the strip when flags fired, the calm line
    // when none did, and which one depends on the seeded patient.
    for (const part of ["spine-diagnosis", "spine-allergies", "red-flag-strip", "no-red-flags"]) {
      const el = page.getByTestId(part);
      if ((await el.count()) === 0) continue;
      const box = await el.boundingBox();
      expect(box!.y + box!.height, `${part} was covered by the drawer`).toBeLessThanOrEqual(
        drawerBox!.y + 1,
      );
    }

    // 3. What was said, beside what the machine made of it.
    await expect(page.getByTestId("note-transcript")).toContainText("grade 1 mucositis");
    await expect(page.getByTestId("note-assessment")).not.toBeEmpty();
    await expect(page.getByTestId("note-badge")).toContainText(/unconfirmed/i);

    // The tags, and the grade shown only because the doctor said one.
    await expect(page.getByTestId("note-tags")).toContainText("mucositis");
    await expect(page.getByTestId("note-tags")).toContainText("G1");

    await shot(page, "02-review-drawer");
  });

  test("there is no medicine field, and the screen says why", async ({ page, playwright }) => {
    const request = await playwright.request.newContext();
    await speak(request, doctorToken, patient.visitId);
    await request.dispose();

    await openConsole(page, doctorToken, patient.tokenNo);
    await page.getByTestId("note-resume").click();
    const drawer = page.getByTestId("note-drawer");
    await expect(drawer).toBeVisible();

    // 4. The AC, as a property of the rendered surface.
    //
    // Asserted on the *inputs*, not on the words. An earlier version of this
    // test banned the string "formulary" anywhere in the drawer and failed on
    // the rule sentence itself — which is the one place the word belongs, since
    // it is what tells the doctor where prescribing actually happens. What must
    // not exist is somewhere to type a drug order.
    await expect(drawer).toContainText(/never prescribes/i);
    await expect(drawer).toContainText(/Consult/);

    // Exactly four fields, and they are the four the contract has. A fifth is
    // how this surface would grow a medication row.
    const boxes = drawer.getByRole("textbox");
    await expect(boxes).toHaveCount(4);
    for (const field of ["subjective", "objective", "assessment", "plan"]) {
      await expect(page.getByTestId(`note-${field}`)).toBeVisible();
    }
    // And nothing asks for the parts of a prescription.
    for (const drugField of [/dose/i, /frequency/i, /route/i, /duration/i, /medicine/i]) {
      await expect(drawer.getByLabel(drugField)).toHaveCount(0);
      await expect(drawer.getByPlaceholder(drugField)).toHaveCount(0);
    }

    await shot(page, "03-the-rule");
  });

  test("an edit sticks, and confirming locks the note", async ({ page, playwright }) => {
    const request = await playwright.request.newContext();
    await speak(request, doctorToken, patient.visitId);
    await request.dispose();

    await openConsole(page, doctorToken, patient.tokenNo);

    // How many are waiting before this one is dealt with. Read rather than
    // assumed: this project runs serially against one patient, so the earlier
    // tests have left drafts of their own on the same visit.
    const before = Number(await page.getByTestId("note-drafts").innerText());
    await page.getByTestId("note-resume").click();

    const assessment = page.getByTestId("note-assessment");
    await assessment.fill("Tolerating AC-T; mucositis settling on rinses.");
    await assessment.blur();

    // The review is a diff: where the doctor differs from the model, it says so.
    await expect(page.getByTestId("edited-assessment")).toBeVisible();
    await shot(page, "04-edited");

    await page.getByTestId("note-confirm").click();
    await expect(page.getByTestId("note-drawer")).toBeHidden();

    // A confirmed note stops being one of the things waiting for the doctor.
    if (before > 1) {
      await expect(page.getByTestId("note-drafts")).toHaveText(String(before - 1));
    } else {
      await expect(page.getByTestId("note-drafts")).toBeHidden();
      await expect(page.getByTestId("note-count")).toContainText(/note/);
    }

    // And the edit is on the record, not just on the screen.
    const check = await page.request.get(`${API}/notes/visits/${patient.visitId}`, {
      headers: { Authorization: `Bearer ${doctorToken}` },
    });
    const confirmed = (await check.json()).filter(
      (n: { status: string }) => n.status === "confirmed",
    );
    expect(confirmed.length).toBeGreaterThan(0);
    expect(confirmed[confirmed.length - 1].fields.assessment).toContain("settling on rinses");
    // The words are untouched by every edit above them.
    expect(confirmed[confirmed.length - 1].transcript).toBe(SPOKEN);

    await shot(page, "05-after-confirm");
  });

  test("a note that could not be mapped still keeps the words and reaches a confirmation", async ({
    page,
    playwright,
  }) => {
    // The degraded state, produced the way the model being down produces it:
    // the words are stored and the mapping never happens. The dock opens the
    // fields itself on the way in, which is the same `compose` the live
    // failure path calls — so this is the state a doctor actually meets, not a
    // convenient approximation of it.
    const request = await playwright.request.newContext();
    await speak(request, doctorToken, patient.visitId, SPOKEN, { map: false });
    await request.dispose();

    await openConsole(page, doctorToken, patient.tokenNo);
    await page.getByTestId("note-resume").click();
    const drawer = page.getByTestId("note-drawer");
    await expect(drawer).toBeVisible();

    // 5. The words are on screen; the fields are open and empty.
    await expect(page.getByTestId("note-transcript")).toContainText("grade 1 mucositis");
    await expect(page.getByTestId("note-assessment")).toBeEmpty();
    // And nothing on screen credits a model with a note no model touched — the
    // first screenshot of this state badged it "AI-drafted".
    await expect(page.getByTestId("note-badge")).not.toContainText(/AI/i);

    // Confirming is refused while it says nothing — that would be a note
    // indistinguishable from one tapped through.
    await expect(page.getByTestId("note-confirm")).toBeDisabled();

    await page.getByTestId("note-objective").fill("Grade 1 mucositis. Typed — the model was down.");
    await page.getByTestId("note-objective").blur();
    await expect(page.getByTestId("note-confirm")).toBeEnabled();
    await shot(page, "06-degraded");

    await page.getByTestId("note-confirm").click();
    await expect(page.getByTestId("note-drawer")).toBeHidden();
  });
});
