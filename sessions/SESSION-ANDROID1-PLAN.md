# SESSION-ANDROID1 — Signed APK, Download, And Environment Pairing

Type: execution only  
Branch: `android-pairing-release`  
Predecessor: accepted `aws-gpu-free-standby`  
Outcome: one downloadable signed APK that pairs safely with Omen or AWS

## Start

Follow `docs/07-SESSION-PROTOCOL.md`. Read only:

1. `HANDOFF.md`
2. `STATE.md`
3. Android sections of `docs/03-FEATURE-SPEC.md`
4. `docs/04-UIUX-GUIDE.md`
5. `docs/16-VOICE-CLOUD-ANDROID-EXECUTION-PLAN.md`
6. `sessions/SESSION-VOICE1.md`
7. `sessions/SESSION-CLOUD1.md`
8. this file

Create the branch from the accepted CLOUD1 commit. Confirm both public HTTPS
environment URLs and their `/api/health` responses before editing.

Android requires every installable/updateable APK to be signed, and release signing
material must be kept outside shared build files. Follow:

- [Android app signing](https://developer.android.com/studio/publish/app-signing)
- [Android build variants and signing configuration](https://developer.android.com/build/build-variants)
- [APK signature verification](https://developer.android.com/tools/apksigner)

## Build contract

### Unit 1 — Runtime environment pairing

Replace the single compiled `API_BASE_URL` with a production allow-list containing
two entries:

- `omen` — display name plus approved HTTPS API base;
- `aws` — display name plus approved HTTPS API base.

Inject the two URLs at build time. Do not hardcode temporary IP addresses. Release
builds accept only HTTPS and only the allow-listed hosts. Debug builds may retain
localhost/emulator support.

Add a small operator-only environment screen, reachable before login and from a
protected settings gesture/menu. It must:

1. show Omen/AWS name and non-sensitive URL;
2. test `/api/health`, environment identity, API compatibility, and clock skew;
3. require explicit confirmation before changing;
4. cancel in-flight requests, clear access/refresh tokens, and reset client state;
5. preserve unsynced offline intakes and label the environment that owns them;
6. refuse to upload an Omen-owned offline intake to AWS or vice versa;
7. persist the selection in DataStore.

Add a server environment-identity response with stable environment ID, human name,
API contract version, release SHA, and current time. It contains no infrastructure
or database secret.

There is no database field, database URL, or direct PostgreSQL driver in Android.

Commit: `S ANDROID1: add safe Omen and AWS pairing`

### Unit 2 — Release signing and versioning

Configure Gradle release signing from environment variables or an ignored local
properties file:

- keystore path;
- store password;
- key alias;
- key password.

The build must fail clearly when release signing inputs are absent. Never commit the
keystore or passwords. Create and document two offline backups of the signing key;
losing it prevents future APK updates under the same application ID.

Increment `versionCode` monotonically and use a release `versionName` tied to this
session. Generate:

- signed, zip-aligned release APK;
- `apksigner verify --verbose --print-certs` report;
- SHA-256 checksum;
- JSON manifest with version code/name, package ID, min SDK, release SHA, size,
  certificate fingerprint, checksum, and build timestamp.

Retain the existing 15 MB gate unless the measured signed artifact proves a justified
change is required. Do not weaken R8/network-security settings to make the build pass.

Commit: `S ANDROID1: produce verifiable signed release APK`

### Unit 3 — Safe HTTPS download

Do not commit the APK binary to Git. Extend the release/deploy script to place:

```text
/data/releases/opd-patient-<version>.apk
/data/releases/opd-patient-<version>.json
/data/releases/opd-patient-<version>.sha256
```

and atomically update stable symlinks:

```text
opd-patient-latest.apk
opd-patient-latest.json
opd-patient-latest.sha256
```

nginx serves them at `/downloads/` with directory listing disabled, TLS required,
correct APK/JSON MIME types, checksum headers where practical, and cache rules that
keep versioned artifacts immutable while the `latest` manifest is short-lived.

Add a simple download page that shows version, size, checksum, release date,
certificate fingerprint, Android 8+ requirement, and installation instructions.
Do not claim Google Play review or Play Protect approval.

Deploy the same signed artifact and manifest to Omen and AWS. The APK itself can pair
with either environment; do not build separately signed “Omen” and “AWS” apps.

Commit: `S ANDROID1: publish one APK from Omen and AWS`

### Unit 4 — Device and failure-mode tests

Automated tests:

- environment allow-list parsing and HTTPS enforcement;
- health/compatibility checks and unreachable/mismatched server errors;
- auth/token clearing on environment change;
- request routing after a switch;
- offline intake environment ownership;
- refusal to cross-sync offline PII;
- process death/relaunch persistence;
- release build, size gate, checksum, manifest, and signature verification.

Physical target-tablet matrix:

1. fresh install from Omen download page;
2. pair to Omen, log in, complete online intake using each accepted profile;
3. complete offline intake, reconnect, and verify Omen sync;
4. switch to AWS, verify logout/reset, complete online intake;
5. create an AWS offline intake and sync only to AWS;
6. make AWS unreachable and verify an honest, recoverable error;
7. install a higher `versionCode` over the existing app and confirm data migration;
8. exercise portrait/landscape, on-screen keyboard, microphone permission denial,
   slow network, and certificate validation;
9. download the same artifact from AWS and verify identical SHA-256;
10. confirm neither app storage nor logs contains database/vendor/signing secrets.

Capture screenshots, device model/Android version, server release SHAs, APK checksum,
certificate fingerprint, and operator acceptance. Close as
`sessions/SESSION-ANDROID1.md`.

Commit: `S ANDROID1: prove paired APK on Omen and AWS`

## Omen deployment — simple operator path

Build the APK on a controlled signing workstation, not on the public server:

```bash
git fetch origin
git switch android-pairing-release
git pull --ff-only origin android-pairing-release
cd android
./gradlew clean testReleaseUnitTest assembleRelease checkApkSize
```

Verify the APK and generate the release bundle using the committed release script.
Transfer only the signed APK, JSON manifest, and checksum to the Omen release staging
directory. Then run the committed publish command, which verifies checksum/signature
before atomically updating `/data/releases` links. Reload—not replace—nginx after
`nginx -t` passes.

Smoke:

```bash
curl -fsSI https://opd.radpretation.ai/downloads/opd-patient-latest.apk
curl -fsS https://opd.radpretation.ai/downloads/opd-patient-latest.json
sha256sum /data/releases/opd-patient-latest.apk
```

Rollback the download by repointing the stable links to the previous signed version.
Do not delete the current or previous artifact, and never change the signing key to
work around an installation failure.

## Acceptance checklist

- [ ] One signed APK pairs to either approved HTTPS API.
- [ ] The APK never connects directly to a database.
- [ ] Switching environments clears auth and cannot cross-sync offline PII.
- [ ] Signature, certificate fingerprint, manifest, checksum, and size pass.
- [ ] Omen and AWS serve byte-identical APKs.
- [ ] Fresh install and in-place upgrade pass on the target tablet.
- [ ] Online/offline/re-sync and failure-mode matrix passes on both environments.
- [ ] Signing material and all server/provider secrets remain outside Git/APK.
- [ ] All repository gates are green.

