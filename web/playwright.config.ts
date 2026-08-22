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
    {
      name: "conformance",
      testMatch:
        /(conformance|offline-db|offline-destination|print|pass|pass-raster|allergy-line|care-system)\.spec\.ts/,
    },
    {
      name: "kiosk",
      testMatch: /kiosk\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The intake boarding pass (doc 23). Live stack; this project *is* the
      // session AC — the patient sees the paper before they are handed it, the
      // pass carries the six things the pilot asked for, Print puts a real
      // ESC/POS raster on the bridge and then says Re-print, and the browser
      // path lays down one 80 x 200mm page. Needs the dev server started with
      // `NEXT_PUBLIC_PRINT_BRIDGE_URL=http://127.0.0.1:9110/print` — the route
      // is intercepted, so no daemon has to exist. Run explicitly
      // (`npm run e2e:pass`); it creates a real visit and token.
      name: "pass-ui",
      testMatch: /pass-ui\.spec\.ts/,
      // Every test here walks a whole intake against a live api, and against a
      // dev server the first compile of `/kiosk` can eat the file default on
      // its own — same reason `notes` raised it.
      timeout: 180_000,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // S-UX.6 viewport smoke: the kiosk surface on portrait and laptop.
      name: "ux-smoke",
      testMatch: /ux-smoke\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "accessibility",
      testMatch: /accessibility\.spec\.ts/,
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
      // SESSION-AYUR-3: the same console under a department whose care system is
      // ayurveda (doc 24 §6). Live stack + `seed_ayurveda_demo`; this project
      // *is* half the session AC — the other half is that `dictation` and
      // `doctor` still pass untouched. Run explicitly (`npm run e2e:ayurveda`).
      name: "ayurveda",
      testMatch: /ayurveda-console\.spec\.ts/,
      // Against a dev server the first compile of `/doctor` can outlast the file
      // default on its own — the same trap `pass-ui` documents above.
      timeout: 180_000,
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
      // Allergy capture (SESSION-ALLERGY). Same live stack + `seed_doctor_demo`
      // as `doctor`; this project *is* the session AC — three states that never
      // collapse into each other, on the one line a doctor prescribes from. Run
      // explicitly (`npm run e2e:allergy`). It writes statements onto the seeded
      // patient's record, so re-seed before re-running.
      name: "allergy",
      testMatch: /allergy\.spec\.ts/,
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
    {
      // The MRD1 coordinator scanner (doc 21 §1.2). Live stack + `make seed`;
      // this project *is* the session's frontend AC — a report is filed against
      // the tapped patient, the page count is the server's, and a failed upload
      // blocks the finish. Run explicitly (`npm run e2e:scan`); it files real
      // documents on whatever database it points at.
      name: "scan",
      testMatch: /scan\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 414, height: 896 } },
    },
    {
      // The MRD2 doctor surface (doc 21 §1.5). Live stack with `MRD_ENABLED=true`
      // + `seed_doctor_demo`; this project *is* the session AC — the spine says
      // what is on file before the doctor opens anything, the reading is a draft
      // until it is reviewed, and the original page is one tap from the number.
      // Run explicitly (`npm run e2e:reports`); it files real documents and
      // records a real verification on whatever database it points at.
      name: "reports",
      testMatch: /reports\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The M3 PACS stub (plan §2). Live stack with PACS_ENABLED=true and
      // PACS_PROVIDER=fake, plus `seed_doctor_demo`; this project *is* the
      // session's frontend AC — the studies list, the spine states the count
      // before the doctor opens anything, the viewer handoff carries a study
      // UID and nothing else, and every empty list says which of the four
      // reasons it is empty for. Run explicitly (`npm run e2e:imaging`).
      name: "imaging",
      testMatch: /imaging\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The M5 research assistant (plan §4). Live stack + `seed_doctor_demo`;
      // this project *is* the session's frontend AC — the doctor sees what
      // will be sent before it is sent, a line they untick is struck rather
      // than hidden and does not reach the server, M4's confirmed tags arrive
      // as context, and a provider outage closes the composer with nothing
      // queued. Run explicitly (`npm run e2e:research`); it writes real
      // research threads and clinical notes on whatever database it points at.
      name: "research",
      testMatch: /research\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The M4 ambient note (plan §3). Live stack + `seed_doctor_demo`; this
      // project *is* the session's frontend AC — the mic survives every tab, the
      // review leaves the spine on screen, an edit sticks, and there is nowhere
      // in the drawer to write a prescription. Run explicitly
      // (`npm run e2e:notes`); it writes real clinical notes on whatever
      // database it points at.
      name: "notes",
      testMatch: /notes\.spec\.ts/,
      // Longer than the file default: every test here re-opens the console, and
      // against a dev server the first compile of `/doctor` alone can eat the
      // 60s budget before a single assertion runs.
      timeout: 120_000,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // Arrival identity + assignment (AR3, sessions/SESSION-ASSIGN-RX-PLAN.md).
      // This project *is* the session AC: a returning patient is recognised
      // without the kiosk disclosing anything, a coordinator unlocks the strip
      // with a PIN and settles identity + doctor in one action, and the desk can
      // do the same for an arrival the strip skipped. Live stack + `make seed`
      // (it uses the seeded PIN holder). Run explicitly (`npm run e2e:assign`).
      name: "assign",
      testMatch: /assign\.spec\.ts/,
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
