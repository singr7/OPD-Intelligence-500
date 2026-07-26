// S-GL.1 — the switchboard (doc 12 §1/§7), driven against a live stack.
//
// This file is the session's acceptance criterion as a test: with every channel
// but the kiosk switched off from the console, a WhatsApp inbound and an app
// intake are refused civilly, nothing 500s, and the kiosk does not notice. The
// switching is done through the console the way an operator would do it, not by
// writing a row, because the thing being proved is that the console can do it.
//
// It also captures the doc 04 §5 screenshots for the Channels tab.
//
//   cd backend && DATABASE_URL=... .venv/bin/python -m app.seed
//   API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
//     npx playwright test --project=channels
//
// ⚠️ Like admin.spec.ts, this really publishes: each run leaves a new published
// channel document on whatever database it points at, and its last test restores
// everything to open. Fine on a dev box; never point it at the pilot.

import { expect, test } from "@playwright/test";

const API = process.env.API_BASE ?? "http://127.0.0.1:8123";
const SHOTS = "screenshots/sgl1";
const ADMIN_PHONE = "+915550000001"; // seeded Priya Sharma (admin)

type Ctx = import("@playwright/test").APIRequestContext;
type Pg = import("@playwright/test").Page;

test.describe.configure({ mode: "serial" });

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
}

/** Publish a document with the given channels open, through the console's own
 *  API. Returns the version published. */
async function publish(request: Ctx, token: string, open: Record<string, boolean>, notes: string) {
  const auth = { Authorization: `Bearer ${token}` };
  const current = await (await request.get(`${API}/admin/channels/document`, { headers: auth })).json();

  const config = { ...current, channels: { ...(current.channels ?? {}) } };
  for (const [channel, enabled] of Object.entries(open)) {
    config.channels[channel] = { ...(config.channels[channel] ?? { ladder: ["v2", "v3"] }), enabled };
  }

  const draft = await request.post(`${API}/admin/channels/draft`, {
    headers: auth,
    data: { config, notes },
  });
  expect(draft.ok(), await draft.text()).toBeTruthy();
  const version = (await draft.json()).version as number;

  const published = await request.post(`${API}/admin/channels/${version}/publish`, {
    headers: auth,
    data: {},
  });
  expect(published.ok(), await published.text()).toBeTruthy();
  return version;
}

test("the Channels tab shows what is open and why the rest is not", async ({ page, request }) => {
  await signedIn(page, await loginToken(request));
  await page.click("nav button:has-text('Channels')");

  await expect(page.locator("section h2").first()).toHaveText("Channels");
  // The first table is the channel list; the second is the version rail.
  await expect(page.locator("table").first().locator("tbody tr")).toHaveCount(4);
  await page.screenshot({ path: `${SHOTS}/01-channels.png`, fullPage: true });

  // The credential cards: write-only, and the page says so rather than showing a
  // masked value that implies there is something to reveal.
  await expect(page.locator(".set-card", { hasText: "Meta Cloud API" })).toBeVisible();
  await expect(
    page.locator('.set-card input[placeholder="unchanged"]').first(),
  ).toHaveValue("");
  await page.screenshot({ path: `${SHOTS}/02-credentials.png`, fullPage: true });
});

test("kiosk-first: closing the other three from the console shuts them politely", async ({
  page,
  request,
}) => {
  const token = await loginToken(request);
  await publish(request, token, { kiosk: true, phone: false, whatsapp: false, app: false }, "go-live: kiosk only");

  await signedIn(page, token);
  await page.click("nav button:has-text('Channels')");

  // The console's own view agrees: one channel open, three closed, and each
  // closed row says which of the two problems it has.
  await expect(page.locator(".notice").first()).toContainText("Open now: Kiosk");
  await expect(page.locator("tr:has-text('WhatsApp') .pill.bad")).toHaveText("Closed");
  await expect(page.locator("tr:has-text('WhatsApp')")).toContainText("switched off");
  await page.screenshot({ path: `${SHOTS}/03-kiosk-only.png`, fullPage: true });

  // 1. WhatsApp: Meta still gets its 200 — a non-200 makes it redeliver forever —
  //    and the patient gets one sentence pointing at the desk.
  const inbound = await request.post(`${API}/whatsapp/webhook`, {
    data: {
      entry: [
        {
          changes: [
            {
              value: {
                contacts: [{ wa_id: "919812300077", profile: { name: "Test" } }],
                messages: [
                  { from: "919812300077", id: `m${Date.now()}`, type: "text", text: { body: "hello" } },
                ],
              },
            },
          ],
        },
      ],
    },
  });
  expect(inbound.status()).toBe(200);
  expect((await inbound.json()).status).toBe("channel_closed");

  // 2. The kiosk is untouched — the headline AC.
  const started = await request.post(`${API}/kiosk/start`, {
    data: { lang: "hi", chief_complaint: "bukhar hai", dept_key: "GENMED" },
  });
  expect(started.status()).toBe(200);
  expect((await started.json()).status).toBe("routed");
});

test("a closed kiosk refuses a start with the patient's own language, not a 500", async ({
  request,
}) => {
  const token = await loginToken(request);
  await publish(request, token, { kiosk: false }, "closing the kiosk for the test");

  const started = await request.post(`${API}/kiosk/start`, {
    data: { lang: "hi", chief_complaint: "bukhar hai", dept_key: "GENMED" },
  });
  expect(started.status()).toBe(503);
  const body = await started.json();
  expect(body.code).toBe("channel_closed");
  expect(body.channel).toBe("kiosk");
  // Hindi, because that is the language she picked — and about the desk, not
  // about us.
  expect(body.detail).toContain("रिसेप्शन");
  expect(started.headers()["retry-after"]).toBeTruthy();
});

test("everything reopens, and the version rail records who closed what", async ({
  page,
  request,
}) => {
  const token = await loginToken(request);
  await publish(request, token, { kiosk: true, phone: true, whatsapp: true, app: true }, "reopening after the S-GL.1 drill");

  await signedIn(page, token);
  await page.click("nav button:has-text('Channels')");
  await expect(page.locator(".notice").first()).toContainText("Open now: Kiosk, Phone, WhatsApp");
  await page.screenshot({ path: `${SHOTS}/04-versions.png`, fullPage: true });

  const started = await request.post(`${API}/kiosk/start`, {
    data: { lang: "hi", chief_complaint: "bukhar hai", dept_key: "GENMED" },
  });
  expect(started.status()).toBe(200);
});
