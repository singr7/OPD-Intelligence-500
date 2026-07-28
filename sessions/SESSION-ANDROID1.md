# SESSION-ANDROID1 — Signed APK, Download, And Environment Pairing

**Date:** 2026-07-28 · **Scope ref:** `sessions/SESSION-ANDROID1-PLAN.md`

## Acceptance criteria checklist

- [x] One Android codebase and the locally signed test artifact can select either
  approved HTTPS environment; release validation rejects any other host.
- [x] Android contains no database URL, database field, or PostgreSQL driver.
- [x] Changing environments re-probes identity/compatibility/skew, requires
  confirmation, cancels requests, clears authentication and server-scoped cache,
  and cannot upload an offline intake owned by the other environment.
- [x] A disposable signed release APK passed release tests, R8, zip alignment,
  APK Signature Scheme v2/v3 verification, certificate fingerprint, JSON manifest,
  checksum, and the 15 MB size gate.
- [x] Signing material and server/provider secret values remain outside Git and
  the generated test APK; the disposable key and artifact were removed after proof.
- [ ] Production signing key and its two encrypted offline backups: not created
  because production key custody was not available in this session.
- [ ] Omen and AWS byte-identical public downloads: not run because both approved
  hostnames failed DNS resolution from this environment.
- [ ] Fresh install, in-place upgrade, and the online/offline/failure-mode tablet
  matrix: not run because `adb` found no attached target or emulator.
- [ ] Full application suite: intentionally not repeated at the user's direction;
  focused ANDROID1 gates and the web production build are green.

## What was built

- The backend `/environment` response exposes only stable environment ID, human
  name, API contract version, release SHA, and server time. Production settings
  accept only `omen` or `aws`.
- Android release configuration consumes Omen/AWS API bases at build time and
  validates two distinct HTTPS `/api` endpoints on the approved hosts. Debug alone
  retains localhost support.
- A pre-login and protected signed-in pairing screen probes health, identity,
  contract compatibility, and clock skew. DataStore persists the selection.
- Room schema v2 labels pending doses and offline intakes with their owning
  environment. Switching preserves those rows but cross-environment sync refuses
  them.
- Release signing comes from environment variables or ignored local properties and
  fails clearly when any input is missing. Versioning is `versionCode` 2 and
  `versionName` `1.0.0-android1`.
- The signed-release script builds, verifies, size-checks, and emits the APK, JSON
  manifest, SHA-256 file, and verification report. Production documentation makes
  two encrypted offline signing-key backups a release prerequisite.
- nginx serves immutable versioned Android artifacts and short-lived `latest`
  pointers without directory listing. Publish verifies package metadata, checksum,
  size, and certificate before atomically changing links; rollback only repoints
  retained artifacts.
- `/download` provides an Android 8+ patient installation guide and displays an
  honest unavailable state until a real release manifest is published.

## Decisions made

- Pairing is an environment identity decision, not a free-form URL field. The APK
  cannot be redirected to an arbitrary server.
- Environment ownership travels with offline PII. Logout/reset on a switch does not
  delete unsynced intake data, but no client may send it to a different environment.
- One application ID, signing lineage, APK, and checksum serve both environments.
  Separate Omen/AWS builds would create needless update and trust divergence.
- A disposable short-lived test certificate can prove the release pipeline, but it
  cannot stand in for production key custody or an updateable public artifact.

## Deviations from spec

- The planned final commit said “prove paired APK on Omen and AWS.” That claim would
  be false without resolvable public environments, a production signing key, and a
  tablet. The evidence commit and session close use explicitly local wording.
- No APK binary or disposable signing material was retained or committed.
- The in-app browser could not initialize because its sandbox metadata lacked a
  required policy value. The download page was instead exercised through the
  repository Playwright setup and visually inspected at desktop and mobile sizes.

## Tests & evidence

- Backend environment response: **3 passed**.
- Android `testDebugUnitTest`: green, including allow-list/HTTPS validation,
  unreachable/identity/compatibility/skew errors, persisted selection, token/cache
  reset, post-switch routing, and offline ownership/cross-sync refusal.
- `android/scripts/test_release_manifest.py`: **1 passed**.
- `deploy/aws/test-contract.sh`: secret converter **4**, drill report **4**, Android
  and Compose/deploy contracts green.
- Web `npm run build`: production build green; `/download` statically rendered.
- ShellCheck 0.10.0: Android build/publish/rollback scripts green.
- Secret-pattern scan and `git diff --check`: green.
- Disposable clean-worktree release proof at `48229e767d775fda974342391723299faefebbb9`:
  1,643,142-byte APK (1.57 MB), package `ai.radpretation.opd`, version code 2,
  version `1.0.0-android1`, signature schemes v2/v3 verified, APK SHA-256
  `75f2df60003c16cc74923f0fb892c17187850449fd91e893f38ca6a865b0f0ea`.
  The disposable certificate fingerprint was
  `69ef39ea28ad8d6e67d1673602e3467fc0f4066caa103b0da7a4cb6df451e982`;
  it is test evidence only and must never be used for production.
- Download-page screenshots:
  `web/screenshots/android1/download-unavailable.png` and
  `web/screenshots/android1/download-unavailable-mobile.png`.
- `adb devices`: no device available.
- Public checks for `https://omen.opd.radpretation.ai/api/health` and
  `https://aws.opd.radpretation.ai/api/health`: DNS resolution failed.

## Known gaps / stubs introduced

- There is no production signing lineage until a custodian generates the key,
  records its certificate fingerprint, and independently verifies two encrypted
  offline backups.
- `/download` deliberately has no active APK link until the production artifact,
  manifest, and checksum are published.
- Real Omen/AWS identity responses, TLS paths, byte equality, and rollback remain
  an external release gate.
- The target-tablet fresh install, signed upgrade/migration, orientation, keyboard,
  permission denial, slow-network, certificate, and dual-environment intake matrix
  remains an external release gate.

## Commits

- `ab6d036` — S ANDROID1: add safe Omen and AWS pairing
- `8c923af` — S ANDROID1: add verifiable release signing
- `05fbaea` — S ANDROID1: add one-APK publishing for Omen and AWS
- `48229e7` — S ANDROID1: strengthen pairing and release verification
- `5135c4a` — S ANDROID1: record local APK and download evidence
- final close commit — S ANDROID1: session close — release controls locally green

---
