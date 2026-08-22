// SESSION-AYUR-3: the same console, handed a department that practises a
// different system of medicine (doc 24 §6).
//
// The claim under test is doc 24's architectural one — "derive, don't fork".
// There is no ayurveda route group, no second console and no second dictation
// panel. `web/app/(doctor)/doctor` reads capability flags off the day payload
// and renders accordingly, and what this file proves is that the *rendering*
// actually differs: the cycle-shaped surfaces are gone, the ayurveda ones are
// there, and a dictated churna is checked against the ayurveda shelf of the
// formulary rather than the oncology one.
//
// Its sibling proof lives in `dictation.spec.ts` and `doctor.spec.ts`, which are
// untouched by AYUR-3 and must stay green: the oncology console renders exactly
// as it did. Both halves are the acceptance criterion; neither is on its own.
//
//   cd backend && .venv/bin/python -m scripts.seed_ayurveda_demo
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=ayurveda
//
// The stack runs with the default `LLM_PROVIDER=fake`, whose canned reply for
// `dictation_map_ayurveda` deliberately includes one preparation that is *not*
// on the ayurveda shelf — a demo where nothing is ever flagged teaches the
// wrong thing about this screen, in either system of medicine.

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/ayur3";
const DOCTOR_PHONE = "+915550001901"; // Dr. Sunita Sharma (AYUR), from the demo script
let cachedAccessToken: string | null = null;

// What a vaidya would actually say at the end of this consult: Hinglish, a
// classical preparation, an anupana and a pathya line, and no cycle anywhere.
const NOTE =
  "Isko amlapitta hai, teen mahine se. Avipattikar churna teen gram BD do hafte, " +
  "Kamdudha ras BD. Garam paani ke saath khane ke baad lein. " +
  "Teekha aur tala hua band karein. Do hafte baad dikhayein.";

test.describe.configure({ mode: "serial" });

// One row each: signing is terminal, so tests that shared a patient would pass
// once and 409 on the next run. Same rule as the dictation suite.
const ROW = { renders: 0, dictates: 0, types: 1 };

/** Sign in as the ayurveda physician, and **fail here** if that does not work.
 *
 *  The retry is for the OTP cooldown: the API refuses a second code for the same
 *  phone within 30 seconds, which a re-run of this file hits every time. Without
 *  it the request comes back with no `debug_code`, the token is `undefined`, the
 *  console 401s and signs itself out — and the first thing that *fails* is an
 *  assertion about a name in the appbar, which reads as "the ayurveda console is
 *  broken" and is nothing of the sort. A test that cannot log in should say so.
 */
async function loginToken(request: import("@playwright/test").APIRequestContext): Promise<string> {
  if (cachedAccessToken) return cachedAccessToken;
  for (let attempt = 0; attempt < 3; attempt++) {
    const req = await request.post(`${API}/auth/otp/request`, { data: { phone: DOCTOR_PHONE } });
    const body = await req.json();
    const code = body.debug_code as string | undefined;
    if (!code) {
      // "wait 30s between OTP requests" — the only expected reason, and it
      // passes on its own.
      await new Promise((resolve) => setTimeout(resolve, 31_000));
      continue;
    }
    const ver = await request.post(`${API}/auth/otp/verify`, {
      data: { phone: DOCTOR_PHONE, code },
    });
    // Read the body once — it is a stream, and a second `.json()` for an error
    // message would throw over the top of the failure it was meant to explain.
    const verified = await ver.json();
    expect(
      verified.access_token,
      `could not sign in as ${DOCTOR_PHONE}: ${JSON.stringify(verified)}`,
    ).toBeTruthy();
    cachedAccessToken = verified.access_token as string;
    return cachedAccessToken;
  }
  throw new Error(
    `could not obtain an OTP for ${DOCTOR_PHONE} — is OTP_DEBUG_ECHO on, and is the stack up?`,
  );
}

async function openConsole(
  page: import("@playwright/test").Page,
  token: string,
  row: number,
): Promise<void> {
  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/doctor");
  // Generous: in dev this is the first hit on `/doctor` and the route compiles
  // before it answers. The assertion is about who the console says it belongs
  // to, not about how fast a dev server is.
  await expect(page.locator(".appbar strong")).toHaveText("Dr. Sunita Sharma", {
    timeout: 60_000,
  });
  await page.getByTestId("scope-department").click();
  const station = page.locator(".station").nth(row);
  await expect(station).toBeVisible();
  const name = await station
    .locator(".sname")
    .evaluate((el) => (el.childNodes[0]?.textContent ?? "").trim());
  await station.locator(".srow").click();
  await expect(page.locator('[data-testid="context-spine"] h1')).toHaveText(name);
}

test("the console opens on an ayurveda department and says whose it is", async ({
  page,
  request,
}) => {
  await openConsole(page, await loginToken(request), ROW.renders);

  await expect(page.locator(".appbar")).toContainText("Ayurveda");
  await expect(page.getByTestId("context-spine")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-worklist.png`, fullPage: true });
});

test("the guideline the lookup names is the one this department follows", async ({
  page,
  request,
}) => {
  // Framing, and only framing (doc 24 §6): the tab is not live in either system
  // and the research assistant's refusals do not vary. But "NCCN Guidelines" on
  // an ayurveda console is a promise about the wrong body, and a doctor reading
  // it would reasonably conclude this console was built for somebody else.
  await openConsole(page, await loginToken(request), ROW.renders);

  await page.getByTestId("coming-soon").click();
  const panel = page.getByTestId("coming-soon-panel");
  await expect(panel).toContainText("AYUSH Guidelines");
  await expect(panel).not.toContainText("NCCN");
});

test("a dictated churna is known, and one off the shelf is flagged", async ({ page, request }) => {
  // The formulary scope, end to end (doc 24 §6.3). Both directions matter: a
  // preparation flagged "not in formulary" during an ayurveda consult is the
  // flag becoming noise, which is how a doctor learns to clear flags without
  // reading them.
  await openConsole(page, await loginToken(request), ROW.dictates);

  await page.keyboard.press("d");
  await expect(page.locator(".dict h2")).toHaveText("Consult note");
  await page.fill(".dict-transcript", NOTE);
  await page.click(".dict-map");
  await expect(page.locator(".med").first()).toBeVisible({ timeout: 15_000 });

  // On the ayurveda shelf, so no flag.
  const known = page.locator(".med", { hasText: "Avipattikar Churna" });
  await expect(known).toBeVisible();
  await expect(known.locator(".med-flag")).toHaveCount(0);

  // Not on it — flagged, and carrying exactly the characters that were heard.
  const flagged = page.locator(".med", { hasText: "Shankh Bhasma Vishesh" });
  await expect(flagged).toBeVisible();
  await expect(flagged.locator(".med-name")).toHaveText("Shankh Bhasma Vishesh");

  await page.screenshot({ path: `${SHOTS}/02-formulary-scope.png`, fullPage: true });
});

test("the cycle-shaped surfaces are absent, and the ayurveda ones are present", async ({
  page,
  request,
}) => {
  // The heart of it. Every one of these is a capability flag, not a comparison
  // against "ayurveda" — see `web/app/_lib/careSystem.ts`, and the conformance
  // test that fails if any component names a member.
  await openConsole(page, await loginToken(request), ROW.dictates);
  await page.keyboard.press("d");
  await expect(page.locator(".dict h2")).toHaveText("Consult note");

  // `ayurvedaAssessment` — five fields the doctor types and no model writes.
  for (const label of ["Prakriti", "Vikriti", "Agni", "Koshtha", "Nidana"]) {
    await expect(page.getByLabel(label)).toBeVisible();
  }

  // `pathyaApathya` — its own field, because it prints under its own heading.
  await expect(page.getByLabel("Pathya – Apathya")).toBeVisible();

  // `showsRegimenEvents` — no "Treatment: cycle 4 · AC-T" line, which in an
  // ayurveda consult would be a claim about a record that does not exist.
  await expect(page.locator(".prov-label", { hasText: /^Treatment$/ })).toHaveCount(0);

  // `showsCycles` — the check-in trend is "symptom across cycles". In a
  // department without cycles it is not an empty chart, it is the wrong chart.
  await page.keyboard.press("d");
  await page.getByTestId("tab-history").click();
  await expect(page.locator(".trends")).toHaveCount(0);
});

test("an ayurveda consult reaches a prescription that carries the diet advice", async ({
  page,
  request,
}) => {
  // Intake → worklist → note → validate → sign → prescription, on fake
  // providers: doc 24 §8's acceptance for this session, minus the cycles.
  await openConsole(page, await loginToken(request), ROW.types);
  await page.keyboard.press("d");
  await expect(page.locator(".dict h2")).toHaveText("Consult note");
  await expect(
    page.locator(".dict-signed"),
    "this row is already signed from an earlier run — re-seed the demo first",
  ).toHaveCount(0);

  await page.getByTestId("type-note").click();
  await expect(page.getByTestId("step-review")).toHaveClass(/is-now/);

  await page.getByTestId("add-med").click();
  await page.getByTestId("add-med-name").fill("Avipattikar Churna");
  await page.getByLabel("Frequency").first().fill("BD");
  await page.getByLabel("Duration").first().fill("14 days");
  await page.getByTestId("add-med-save").click();
  await expect(page.locator(".med-name").first()).toHaveText("Avipattikar Churna");
  // Known without acknowledgement: the ayurveda shelf is what this consult is
  // checked against, so the signature is not blocked.
  await expect(page.locator(".med-flag")).toHaveCount(0);

  await page.getByLabel("Impression").fill("Amlapitta");
  await page.getByLabel("Prakriti").fill("vata-pitta");
  await page.getByLabel("Agni").fill("tikshna");
  await page
    .getByLabel("Pathya – Apathya")
    .fill("Purana chawal, moong dal, chhaas\nTeekha, tala hua, khatta band");
  await page.locator(".dict-review").click(); // blur commits the last field

  await page.screenshot({ path: `${SHOTS}/03-assessment.png`, fullPage: true });

  await expect(page.getByTestId("sign-note")).toBeEnabled();
  await page.getByTestId("sign-note").click();

  await expect(page.getByTestId("prescription-issued")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".rx-name").first()).toContainText("Avipattikar Churna");

  // The signed note is the record, and it must still carry what the doctor
  // typed. This is the assertion that catches the failure this file found the
  // first time it was run: `PatchIn` did not declare the two new fields, so
  // Pydantic dropped them before the service's allowlist was consulted, every
  // request succeeded, and the assessment was silently gone by the time anyone
  // looked at the print.
  const note = page.locator(".dict");
  await expect(note).toContainText("vata-pitta");
  await expect(note).toContainText("tikshna");
  await expect(note).toContainText("Purana chawal, moong dal, chhaas");

  // And the three lines the doctor left blank are absent rather than five
  // dashes under a heading, which would read as five findings of normal.
  await expect(note.locator(".prov-label", { hasText: /^Vikriti$/ })).toHaveCount(0);
  await expect(note.locator(".prov-label", { hasText: /^Koshtha$/ })).toHaveCount(0);

  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${SHOTS}/04-prescription.png`, fullPage: true });
});
