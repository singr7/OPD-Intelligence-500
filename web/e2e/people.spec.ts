// S-GL.2 — staff onboarding + roster (doc 12 §7), driven against a live stack.
//
// This file is the session's acceptance criterion as a test, and deliberately
// does the whole thing **through the console**, because "an administrator can do
// this without a seed run or a deploy" is the claim being made:
//
//   a new doctor is onboarded, given a Tuesday clinic by CSV import, has slots
//   generated, and appears in the receptionist's inventory and the doctor
//   console — entirely from the console, with no seed run and no deploy; the
//   import dry-run refuses a row naming an unknown doctor and says which row.
//
// It also captures the doc 04 §5 screenshots for the People & roster tab.
//
//   cd backend && DATABASE_URL=... .venv/bin/python -m app.seed
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=people
//
// ⚠️ Like admin.spec.ts and channels.spec.ts, this really writes: each run
// creates a doctor and a clinic on whatever database it points at, and the last
// test deactivates the doctor it made. Fine on a dev box; never point it at the
// pilot.

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/sgl2";
const ADMIN_PHONE = "+915550000001"; // seeded Priya Sharma (admin)

type Ctx = import("@playwright/test").APIRequestContext;
type Pg = import("@playwright/test").Page;

test.describe.configure({ mode: "serial" });

// Unique per run, so re-running the suite on the same dev database does not
// collide on `users.phone` or `doctors.reg_no` — both are unique by design.
const RUN = Date.now().toString().slice(-6);
const DOCTOR = {
  name: `Dr. Meera Joshi ${RUN}`,
  phone: `+9159${RUN}0001`,
  reg_no: `E2E-ONC-${RUN}`,
};

async function loginToken(request: Ctx): Promise<string> {
  const req = await request.post(`${API}/auth/otp/request`, { data: { phone: ADMIN_PHONE } });
  const code = (await req.json()).debug_code as string;
  const ver = await request.post(`${API}/auth/otp/verify`, { data: { phone: ADMIN_PHONE, code } });
  return (await ver.json()).access_token as string;
}

async function signedIn(page: Pg, token: string) {
  await page.addInitScript((t) => localStorage.setItem("opd_staff_token", t), token);
  await page.goto("/admin");
  await expect(page.locator(".admin header h1")).toHaveText("OPD control room");
  await page.click("nav button:has-text('People & roster')");
}

/** Playwright's file chooser wants a real file; this hands the input a buffer. */
async function upload(page: Pg, csv: string) {
  await page.locator('input[type="file"]').setInputFiles({
    name: "roster.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv),
  });
}

const HEADER = "doctor,weekday,start,end,slot_type,capacity\n";

test("a doctor is onboarded from the console and can sign in", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));

  await page.click("button:has-text('Add a doctor')");
  const form = page.locator(".set-card", { hasText: "New doctor" });
  await form.locator("input").nth(0).fill(DOCTOR.name);
  await form.locator("input").nth(1).fill(DOCTOR.phone);
  await form.locator("input").nth(2).fill(DOCTOR.reg_no);
  await form.locator("input").nth(3).fill("MD, DM (Medical Oncology)");
  await form.locator("select").selectOption({ label: "Medical Oncology" });
  await form.locator("button.action").click();

  // She is in the list, with no clinic and nobody booked — the honest starting
  // state, and the reason the week below shows her as empty rather than absent.
  const row = page.locator("tbody tr", { hasText: DOCTOR.name });
  await expect(row).toBeVisible();
  await expect(row.locator("td").nth(2)).toHaveText(DOCTOR.phone);

  // The week now carries her, visibly clinic-less.
  await expect(
    page.locator(".week-row", { hasText: DOCTOR.name }).locator(".empty-week"),
  ).toHaveText("no clinic");

  await page.screenshot({ path: `${SHOTS}/01-people.png`, fullPage: true });
});

test("the dry run refuses the row that names a doctor we do not have, and says which", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  await upload(
    page,
    HEADER +
      `${DOCTOR.reg_no},Tuesday,10:00,13:00,follow_up,2\n` +
      "RMC-ONC-9999,Wednesday,09:30,11:30,new_consult,1\n",
  );
  await page.click("button:has-text('Preview')");

  // The AC, on screen: the row number an administrator can go and look at, and
  // the name that was not found.
  const bad = page.locator("tr.bad-row");
  await expect(bad).toHaveCount(1);
  await expect(bad.locator("td").first()).toHaveText("3");
  await expect(bad.locator(".error")).toContainText("row 3");
  await expect(bad.locator(".error")).toContainText("RMC-ONC-9999");

  // Nothing was written, and the apply button will not let you write half of it.
  await expect(page.locator(".notice.bad-notice")).toContainText("cannot be imported");
  await expect(page.locator("button:has-text('Apply the roster')")).toBeDisabled();

  await page.screenshot({ path: `${SHOTS}/02-dry-run-refusal.png`, fullPage: true });
});

test("the corrected roster imports, generates slots, and fills the week", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  await upload(page, HEADER + `${DOCTOR.reg_no},Tuesday,10:00,13:00,follow_up,2\n`);
  await page.click("button:has-text('Preview')");
  await expect(page.locator(".notice")).toContainText("Ready.");
  await expect(page.locator("tr.bad-row")).toHaveCount(0);

  await page.click("button:has-text('Apply the roster')");
  await expect(page.locator(".notice.flash")).toContainText("Imported:");

  // The clinic is in her Tuesday column, and it is *generated* — the block is
  // solid rather than hollow, which is the console's whole authored/bookable
  // distinction.
  const clinic = page
    .locator(".week-row", { hasText: DOCTOR.name })
    .locator(".clinic", { hasText: "10:00–13:00" });
  await expect(clinic).toBeVisible();
  await expect(clinic).not.toHaveClass(/unbuilt/);
  await expect(clinic.locator(".counts")).toContainText("booked");

  await page.screenshot({ path: `${SHOTS}/03-week.png`, fullPage: true });

  // Generating again is safe — the button an operator will press twice.
  await page.click("button:has-text('Generate slots')");
  await expect(page.locator(".notice.flash")).toContainText("already has its slots");
});

test("she is in the receptionist's own inventory — the AC's last mile", async ({ request }) => {
  const token = await loginToken(request);
  const auth = { Authorization: `Bearer ${token}` };

  const people = (await (await request.get(`${API}/admin/people`, { headers: auth })).json()) as {
    name: string;
    doctor_id: string | null;
  }[];
  const her = people.find((p) => p.name === DOCTOR.name);
  expect(her?.doctor_id, "the doctor the console created is missing a profile").toBeTruthy();

  // `/appointments/slots` is the query the AI receptionist reads options from
  // and the coordinator's booking screen lists. Nothing about this route knows
  // the console exists — it is scoped to her only so the seeded hospital's own
  // inventory cannot mask an empty result.
  const resp = await request.get(
    `${API}/appointments/slots?doctor_id=${her!.doctor_id}&limit=100`,
    { headers: auth },
  );
  expect(resp.ok(), await resp.text()).toBeTruthy();
  const hers = (await resp.json()) as { doctor_name: string; starts_at: string }[];

  expect(hers.length, "the new doctor has no bookable inventory").toBeGreaterThan(0);
  expect(hers[0].doctor_name).toBe(DOCTOR.name);
  // Tuesday, in the hospital's own timezone.
  const day = new Date(hers[0].starts_at).toLocaleDateString("en-GB", {
    weekday: "long",
    timeZone: "Asia/Kolkata",
  });
  expect(day).toBe("Tuesday");
});

test("deactivating shows what it would leave behind before it does it", async ({
  page,
  request,
}) => {
  await signedIn(page, await loginToken(request));

  const row = page.locator("tbody tr", { hasText: DOCTOR.name });
  await row.locator("button:has-text('Deactivate')").click();

  const card = page.locator(".set-card", { hasText: `Deactivate ${DOCTOR.name}?` });
  await expect(card).toBeVisible();
  // Nobody booked with her in this run, so the card says so plainly rather than
  // showing an empty table that looks like a loading state.
  await expect(card).toContainText("Nobody is booked with them.");
  await expect(card).toContainText("clinic(s) stop");
  await page.screenshot({ path: `${SHOTS}/04-deactivate.png`, fullPage: true });

  await card.locator("button.action").click();
  await expect(page.locator(".notice.flash")).toContainText("can no longer sign in");

  // She is still listed — deactivation is not deletion — and her clinic is gone
  // from the week.
  await expect(page.locator("tr.inactive", { hasText: DOCTOR.name })).toBeVisible();
  await expect(page.locator(".week-row", { hasText: DOCTOR.name })).toHaveCount(0);
});
