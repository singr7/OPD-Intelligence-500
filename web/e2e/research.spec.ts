// M5 — the research assistant, driven against a live stack. This file *is* the
// session's frontend acceptance criterion.
//
// The five things it proves, in the order they matter:
//
//   1. **The doctor sees what will be sent, before anything is sent.** The
//      context strip is on screen with the panel, it names its sources, and no
//      identifier is anywhere in it. This is the module's whole claim.
//   2. **The trim is real and it is subtractive.** Unticking a line strikes it
//      through rather than hiding it, and the line does not reach the server —
//      checked against the *stored* turn, not against the screen.
//   3. **M4's output is M5's input.** A note confirmed through the notes API
//      appears in the context as today's tags. That is the plan's stated
//      reason M5 came after M4 rather than before it, and it is the one claim
//      no unit test can make end to end.
//   4. **The answer is reference, and the screen says so throughout.** The
//      framing is above the conversation, and the spine is still on screen.
//   5. **A provider outage closes the composer and queues nothing.**
//
//   cd backend && .venv/bin/python -m scripts.seed_doctor_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=research
//
// The LLM may stay `fake` — `research_assist` is prose, so the fake's default
// reply exercises the whole path with no vendor.
//
// **Why the outage is faked at the browser and not at the api.** The server's
// 503 and 429 are proven in `backend/tests/test_research_routes.py`, including
// that neither stores anything. What cannot be proven there is what the *panel*
// does when it meets one, and the only way to produce either on demand against
// a live stack is to intercept the response. So the interception is deliberately
// narrow: it fakes the status code and nothing else, and every assertion about
// what was stored is made against the real API afterwards.
//
// ⚠️ It writes real research threads and clinical notes on whatever database it
// points at. Dev boxes only.

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/m5";
const DOCTOR_PHONE = "+915550001001"; // seeded Dr. Anil Gupta (MEDONC)

const SPOKEN =
  "post-chemo cycle 3, tolerating well, grade 1 mucositis, review CBC before the next cycle";

test.describe.configure({ mode: "serial" });

async function shot(page: Page, name: string, anchor = "research-tab") {
  // Scroll the panel under the sticky spine before capturing.
  //
  // Two corrections live in this helper, both from looking at its own output.
  //
  // **Viewport, not `fullPage`.** A full-page capture renders `position: sticky`
  // elements at their scroll offset rather than where a doctor sees them, and
  // the first pass showed the context spine floating over the middle of the
  // panel — a layout bug that did not exist.
  //
  // **And scrolled.** The second pass was honest but useless: at 720px the app
  // bar, spine and red-flag strip fill the viewport, so every screenshot was of
  // the console with two lines of research panel peeking under it. A doctor
  // working in this tab has scrolled it up under the spine, so that is the
  // frame to critique.
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

/** Capture an ambient note and confirm it, exactly as M4's dock does.
 *
 *  This is how the `note_tags` context item comes to exist, and doing it
 *  through the real notes API rather than by writing a row is the point: the
 *  hand-off being proved is the one a doctor actually produces. */
async function confirmANote(request: APIRequestContext, doctor: string, visitId: string) {
  const headers = { Authorization: `Bearer ${doctor}` };
  const started = await request.post(`${API}/notes/visits/${visitId}`, {
    headers,
    data: { transcript: SPOKEN },
  });
  expect(started.status()).toBe(200);
  const noteId = (await started.json()).id;

  const mapped = await request.post(`${API}/notes/${noteId}/map`, { headers });
  expect(mapped.status(), "the mapping failed — check the LLM profile on the api").toBe(200);

  const confirmed = await request.post(`${API}/notes/${noteId}/confirm`, { headers });
  expect(confirmed.status()).toBe(200);
}

async function threadTurns(request: APIRequestContext, doctor: string, visitId: string) {
  const panel = await request.get(`${API}/research/visits/${visitId}`, {
    headers: { Authorization: `Bearer ${doctor}` },
  });
  expect(panel.status()).toBe(200);
  return (await panel.json()).turns as { question: string; context_sent: string[] }[];
}

async function openConsole(page: Page, token: string, tokenNo?: number) {
  await page.goto("/doctor", { waitUntil: "domcontentloaded" });
  await page.evaluate((t) => window.localStorage.setItem("opd_staff_token", t), token);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("context-spine")).toBeVisible({ timeout: 30_000 });
  if (tokenNo != null) {
    await page.getByTestId(`station-${tokenNo}`).click();
    await expect(page.getByTestId("context-spine")).toBeVisible();
  }
}

async function openResearch(page: Page, token: string, patient: Patient) {
  await openConsole(page, token, patient.tokenNo);
  await page.getByTestId("tab-research").click();
  await expect(page.getByTestId("research-tab")).toBeVisible({ timeout: 20_000 });
}

test.describe("the doctor looks something up", () => {
  let doctorToken = "";
  let patient: Patient;

  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    doctorToken = await tokenFor(request, DOCTOR_PHONE);
    patient = await firstPatient(request, doctorToken);
    // 3. M4's output becomes M5's input. Done once, before anything else, so
    // every test below sees the context a real consult would have produced.
    await confirmANote(request, doctorToken, patient.visitId);
    await request.dispose();
  });

  test("the tab graduated, and AI Research left the coming-soon list", async ({ page }) => {
    await openConsole(page, doctorToken, patient.tokenNo);

    await expect(page.getByTestId("tab-research")).toBeVisible();

    // The MRD2 move, repeated: a graduated feature leaves the list rather than
    // sitting in both places, and the line that replaced it must not claim the
    // new tab does something it does not.
    await page.getByTestId("coming-soon").click();
    const soon = page.getByTestId("coming-soon-panel");
    await expect(soon).toBeVisible();
    await expect(soon).not.toContainText("AI Research");
    await expect(soon).toContainText("NCCN Guidelines");
    await expect(soon).toContainText(/not live yet/i);
  });

  test("the panel shows exactly what will be sent, and no identifier is in it", async ({
    page,
  }) => {
    await openResearch(page, doctorToken, patient);

    // 1. The strip is on screen with the panel, and it is *above* the
    // conversation and the question box.
    //
    // Not "there are no turns yet": this project writes real threads and runs
    // serially against one seeded patient, so a re-run legitimately finds the
    // previous run's turns. The M4 spec learned the same thing about draft
    // counts. What matters is not that the thread is empty, it is that the
    // doctor cannot reach the question box without passing what will be sent.
    const strip = page.getByTestId("research-context");
    await expect(strip).toBeVisible();
    const stripBox = await strip.boundingBox();
    const askBox = await page.getByTestId("research-question").boundingBox();
    expect(stripBox!.y, "the context strip sits below the question box").toBeLessThan(askBox!.y);

    // The age band, and the claim about it stated where the claim is made.
    await expect(strip).toContainText(/\d0-\d9|under 18|90\+/);
    await expect(strip).toContainText(/never sent/i);

    // 3. M4's confirmed tags are here, and the grade is carried as something
    // the doctor *said*, never as this system's assessment.
    await expect(page.getByTestId("research-ctx-note_tags")).toBeVisible();
    await expect(strip).toContainText("mucositis");
    await expect(strip).toContainText(/the doctor mentioned grade/i);

    // The identity check, against the whole strip: this patient's real name and
    // MRN are on the spine forty pixels above, and neither may be down here.
    const stripText = (await strip.innerText()).toLowerCase();
    expect(stripText).not.toContain(patient.name.toLowerCase());
    expect(stripText).not.toMatch(/mrn\d/);
    expect(stripText).not.toMatch(/[6-9]\d{9}/);

    // A source that is empty says why, rather than simply not appearing.
    await expect(page.getByTestId("research-absent")).toContainText(/no signed consult note/i);

    // 4. And the framing is above the conversation, not under it.
    const frame = page.getByTestId("research-disclaimer");
    await expect(frame).toContainText(/reference only/i);
    const frameBox = await frame.boundingBox();
    const composerBox = await page.getByTestId("research-question").boundingBox();
    expect(frameBox!.y, "the disclaimer sits below the question box").toBeLessThan(composerBox!.y);

    await shot(page, "01-what-will-be-sent");
  });

  test("opening the tab does not scroll the context strip behind the spine", async ({ page }) => {
    // The M4 lesson, applied to a different surface: assert the geometry, not
    // `toBeVisible`. An element under the sticky spine is still "visible" to
    // Playwright — in the DOM with a non-zero box — which is exactly how the
    // first build of this tab passed its tests while scrolling the context
    // strip out of sight on the way in. The screenshot is what caught it.
    //
    // The visit already has a thread by this point, so this is the state that
    // broke: a doctor coming back to a conversation must land on what is about
    // to be sent, not below it.
    await openResearch(page, doctorToken, patient);
    await page.waitForTimeout(400);

    const spine = await page.getByTestId("context-spine").boundingBox();
    const header = await page.locator(".rsx-ctx-h").boundingBox();
    expect(spine, "no spine on screen").not.toBeNull();
    expect(header, "no context header on screen").not.toBeNull();
    expect(
      header!.y,
      "the panel scrolled its own context strip up behind the spine",
    ).toBeGreaterThanOrEqual(spine!.y + spine!.height - 1);
  });

  test("the note dock's mic does not sit on top of Ask", async ({ page }) => {
    // Two surfaces own the bottom-right of this console: M4's fixed mic dock
    // and this panel's primary action. Without a reserved gutter the mic lands
    // squarely on the Ask button once the doctor scrolls to the end of a
    // conversation — and a button under a floating mic is a button that
    // records an observation when a doctor meant to ask a question.
    //
    // `.rsx`'s bottom padding is what keeps them apart, and it is derived from
    // the dock's own height and offset. This is the assertion that fails if
    // either of those changes without the other.
    await openResearch(page, doctorToken, patient);

    // Checked as **horizontal** clearance, and at several scroll positions.
    //
    // The first version of this test scrolled to the bottom and compared boxes
    // there, which passed against a fix that only worked at maximum scroll — a
    // screenshot then caught the mic sitting on the button at an ordinary
    // scroll position. A vertical check is a check of one accident. The dock is
    // fixed to the right edge, so the property that actually holds is that the
    // button stays left of it, and that is true at every offset or none.
    const dock = await page.locator(".nd-fab-wrap").boundingBox();
    expect(dock, "the note dock is not mounted — has it moved?").not.toBeNull();

    for (const position of ["top", "middle", "bottom"] as const) {
      await page.evaluate((where) => {
        const max = document.body.scrollHeight - window.innerHeight;
        window.scrollTo(0, where === "top" ? 0 : where === "middle" ? max / 2 : max);
      }, position);
      await page.waitForTimeout(250);

      const ask = await page.getByTestId("research-ask").boundingBox();
      expect(ask, "no Ask button on screen").not.toBeNull();
      expect(
        ask!.x + ask!.width,
        `the mic dock overlapped the Ask button at scroll ${position}`,
      ).toBeLessThanOrEqual(dock!.x + 1);
    }
  });

  test("asking keeps the spine on screen and the answer is reference", async ({ page }) => {
    await openResearch(page, doctorToken, patient);

    // Tick everything first, rather than trusting the thread to arrive
    // untrimmed. A previous run of this project may have left the doctor's
    // trim stored on the thread — which is the module behaving correctly, and
    // exactly why this cannot be assumed. The state this test needs is its own
    // to establish.
    for (const id of ["demographics", "note_tags"]) {
      const box = page.getByTestId(`research-ctx-${id}`);
      if ((await box.count()) > 0) await box.check();
    }

    await page.getByTestId("research-question").fill("How is anaemia managed during AC-T?");
    await page.getByTestId("research-ask").click();

    const turn = page.getByTestId("research-turn").last();
    await expect(turn).toBeVisible({ timeout: 30_000 });
    await expect(turn).toContainText("How is anaemia managed during AC-T?");

    // 4. The spine — diagnosis, allergies, red flags — is still on screen while
    // the doctor reads. Geometry, not `toBeVisible`: M4 learned that the hard
    // way when a drawer sat squarely on top of a spine the test called visible.
    // A tab cannot overlay it by construction, so what is checked here is that
    // it is still mounted and still on the page rather than scrolled away.
    const spineBox = await page.getByTestId("context-spine").boundingBox();
    expect(spineBox, "the spine unmounted when the tab changed").not.toBeNull();
    expect(spineBox!.height).toBeGreaterThan(0);
    for (const part of ["spine-diagnosis", "spine-allergies"]) {
      await expect(page.getByTestId(part)).toBeVisible();
    }

    // The turn says what left the box with it, and the panel can show it.
    // Scoped to *this* turn, not to the page: a visit accumulates turns across
    // runs of this project, and a page-wide `.last()` is a different element
    // the moment any other turn's list is open.
    await turn.getByTestId("research-sent-toggle").click();
    await expect(turn.getByTestId("research-sent")).toContainText("mucositis");

    // The budget is stated, and it moved.
    await expect(page.getByTestId("research-budget")).toContainText(/left today/);

    await shot(page, "02-an-answer", "research-turn");
  });

  test("unticking a line strikes it through, and it does not leave the box", async ({
    page,
    playwright,
  }) => {
    await openResearch(page, doctorToken, patient);

    // 2. The deliberate risk, asserted as behaviour: the line stays on screen.
    const item = page.getByTestId("research-ctx-note_tags");
    const row = page.locator(".rsx-items > li").filter({ has: item });
    await expect(row).not.toHaveClass(/off/);

    await item.uncheck();
    await expect(row, "the withheld line disappeared instead of being struck").toHaveClass(/off/);
    await expect(row.locator(".rsx-item-t")).toBeVisible();
    await expect(page.getByTestId("research-context-count")).toContainText(/of/);

    await shot(page, "03-withheld-not-hidden");

    const question = "General question with the tags withheld";
    await page.getByTestId("research-question").fill(question);
    await page.getByTestId("research-ask").click();
    await expect(page.getByTestId("research-turn").last()).toContainText(question, {
      timeout: 30_000,
    });

    // The claim is about what the *server* stored, not about what the screen
    // said. A trim that only trimmed the display would pass every assertion
    // above it and still send the line.
    const request = await playwright.request.newContext();
    const turns = await threadTurns(request, doctorToken, patient.visitId);
    await request.dispose();

    const asked = turns.find((t) => t.question === question);
    expect(asked, "the turn was not stored").toBeTruthy();
    expect(
      asked!.context_sent.join(" "),
      "the withheld line was sent anyway",
    ).not.toContain("mucositis");
    // And the line they kept did go.
    expect(asked!.context_sent.join(" ")).toMatch(/\d0-\d9|under 18|90\+/);

    // The trim is remembered when the tab is reopened, rather than silently
    // restoring what they turned off.
    await page.getByTestId("tab-overview").click();
    await page.getByTestId("tab-research").click();
    await expect(page.getByTestId("research-ctx-note_tags")).not.toBeChecked();
  });

  test("a provider outage closes the composer and queues nothing", async ({
    page,
    playwright,
  }) => {
    await openResearch(page, doctorToken, patient);

    // See the header for why this is intercepted rather than produced at the
    // api: narrow, status-code only, and what was stored is checked for real.
    await page.route("**/research/visits/**", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "gemini http 503" }),
      });
    });

    const question = "This question should never be stored";
    await page.getByTestId("research-question").fill(question);
    await page.getByTestId("research-ask").click();

    // 5. The panel says so and closes. Nothing spins, nothing is pending.
    const halt = page.getByTestId("research-halt");
    await expect(halt).toBeVisible({ timeout: 20_000 });
    await expect(halt).toContainText(/unreachable/i);
    await expect(halt).toContainText(/nothing is waiting/i);
    // The vendor's own error string is not a thing to show a doctor.
    await expect(halt).not.toContainText(/gemini|http|503/i);
    await expect(page.getByTestId("research-ask")).toHaveCount(0);
    await expect(page.getByTestId("research-thinking")).toHaveCount(0);

    await shot(page, "04-provider-down", "research-halt");

    // And it is not merely hidden from the screen — the server has no such turn.
    await page.unroute("**/research/visits/**");
    const request = await playwright.request.newContext();
    const turns = await threadTurns(request, doctorToken, patient.visitId);
    await request.dispose();
    expect(turns.map((t) => t.question)).not.toContain(question);
  });
});
