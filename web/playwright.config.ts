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
    { name: "conformance", testMatch: /(conformance|offline-db|print)\.spec\.ts/ },
    {
      name: "kiosk",
      testMatch: /kiosk\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The S7 offline demo (doc 01 §5): needs a live stack, so it is separate
      // from the pure-logic conformance project and run explicitly.
      name: "offline-demo",
      testMatch: /offline-demo\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The S8 queue board + coordinator console (doc 03 §6). Live stack; drives
      // the real WS fan-out to prove board↔console sync. Screenshots for doc 04
      // §5 self-critique. Run explicitly (`npm run e2e:queue`).
      name: "queue",
      testMatch: /queue\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The S9 doctor console (doc 03 §4/§5). Live stack + `seed_doctor_demo`;
      // this project *is* the session AC — a doctor working a full morning.
      // Screenshots for doc 04 §5 self-critique. Run explicitly
      // (`npm run e2e:doctor`).
      name: "doctor",
      testMatch: /doctor\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The S10 consult note (doc 03 §7). Same live stack + `seed_doctor_demo`
      // as `doctor`; this project *is* the session AC — the flag is seen, the
      // signature is refused, the drug keeps its name. Run explicitly
      // (`npm run e2e:dictation`).
      name: "dictation",
      testMatch: /dictation\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The S18-late admin console (doc 03 §10). Live stack + a seeded database;
      // this project *is* the session AC — edit an option in the visual editor,
      // publish, and the intake path serves it. Screenshots for doc 04 §5. Run
      // explicitly (`npm run e2e:admin`).
      name: "admin",
      testMatch: /admin\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The S-GL.1 switchboard (doc 12 §1). Live stack + a seeded database; this
      // project *is* the session AC — close every channel but the kiosk from the
      // console, and watch WhatsApp refuse civilly while the kiosk carries on.
      // Run explicitly (`npm run e2e:channels`); it publishes.
      name: "channels",
      testMatch: /channels\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // Staff onboarding + roster (S-GL.2, doc 12 §7). This project *is* the
      // session AC: onboard a doctor, give her a Tuesday clinic by CSV import,
      // generate her slots, and find her in the receptionist's inventory —
      // entirely from the console. Run explicitly (`npm run e2e:people`); it
      // creates real staff rows on whatever database it points at.
      name: "people",
      testMatch: /people\.spec\.ts/,
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
