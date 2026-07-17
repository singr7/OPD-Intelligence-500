import { defineConfig, devices } from "@playwright/test";

// S6 kiosk screenshot + smoke suite. Drives the real local stack (a running api
// + the seeded dev DB), so it is not part of `npm run typecheck && lint` — it is
// run explicitly (`npm run e2e`) against `make dev`. See docs 04 §5: every
// patient-facing screen is screenshotted and self-critiqued before session close.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    // Headless chromium has no Web Speech — the kiosk falls back to tap-to-type,
    // which is exactly the deterministic path we want to screenshot.
    ...devices["Desktop Chrome"],
    baseURL: process.env.KIOSK_URL ?? "http://127.0.0.1:3210",
    // Landscape 10–11" tab (doc 04 §3: kiosk is landscape) — after the spread so
    // it wins.
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
  },
});
