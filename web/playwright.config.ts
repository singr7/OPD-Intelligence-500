import { defineConfig, devices } from "@playwright/test";

// Two suites live here, and they have very different needs (hence `projects`):
//
// * **conformance** (S7) — pure logic. Replays golden traces from the Python
//   walker through the offline TS one. No browser, no server, so it runs in
//   `make test` on every change: it is the gate that stops the two walkers
//   drifting apart.
// * **kiosk** (S6) — the screenshot + smoke suite. Drives the real local stack
//   (a running api + the seeded dev DB), so it stays out of `make test` and is
//   run explicitly (`npm run e2e`) against `make dev`. See docs 04 §5: every
//   patient-facing screen is screenshotted and self-critiqued before session
//   close.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  reporter: [["list"]],
  projects: [
    // Pure-logic suites: the walker conformance gate and the offline-store
    // invariants. No browser, no server — they run in `make test`.
    { name: "conformance", testMatch: /(conformance|offline-db)\.spec\.ts/ },
    {
      name: "kiosk",
      testMatch: /kiosk\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
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
