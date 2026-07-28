# HANDOFF — after SESSION-ANDROID1

**Repo state:** branch `android-pairing-release`; final close commit is the branch
tip. Focused Android, backend environment, release-manifest, deployment-contract,
ShellCheck, web production-build, secret-scan, and diff-hygiene gates are green.
The pre-existing `web/tsconfig.tsbuildinfo` modification remains intentionally
uncommitted. The full application suite was not repeated at the user's direction.

ANDROID1's repository work is complete: one allow-listed Omen/AWS app, server
identity probing, auth/cache reset on switching, environment-owned offline PII,
external release signing, verified artifact metadata, atomic HTTPS publishing,
recoverable rollback, and an honest patient download page.

## Combined external release gate

No production APK was signed or published, no AWS standby was provisioned, and no
tablet was available. Before calling VOICE1/CLOUD1/ANDROID1 released:

1. Provision CLOUD1 and close its AWS/ECR/DNS/TLS/backup/restore/failover evidence
   exactly as listed in `sessions/SESSION-CLOUD1.md`.
2. Generate the production Android signing key on the controlled workstation,
   record its certificate fingerprint, and independently verify two encrypted
   offline backups before building.
3. Build from the accepted full SHA; retain the signature report, manifest,
   checksum, and size result. Do not reuse the disposable test fingerprint recorded
   in the session log.
4. Publish the exact same APK/manifest/checksum to Omen and AWS and prove the two
   public downloads are byte-identical.
5. Verify both `/api/health` and `/environment` responses over public TLS and record
   environment IDs, contract versions, clocks, and release SHAs.
6. Run the target-tablet matrix: fresh install, Omen online/offline/re-sync, switch
   reset, AWS online/offline/re-sync, unreachable recovery, and higher-versionCode
   in-place upgrade/data migration.
7. Capture model/Android version, screenshots, server SHAs, APK checksum,
   production certificate fingerprint, and operator acceptance.

## Release controls to preserve

- Production pairing is a fixed two-host HTTPS allow-list, never a free-form URL.
- An environment switch must clear authentication/server state while preserving
  environment-owned unsynced intakes; cross-environment upload remains forbidden.
- Omen and AWS serve one byte-identical APK under one application ID and signing
  lineage. Never produce separately signed environment builds.
- `publish-android-release.sh` verifies before atomically repointing `latest`;
  rollback repoints retained artifacts and never deletes the current or previous
  release.
- The download page must stay unavailable until a real manifest exists. Do not
  claim Play review, Play Protect approval, or public availability without evidence.
- CLOUD1's PostgreSQL read-only setting remains the writer boundary; DNS/pairing is
  not a concurrency control.

## Decisions needed from the human

- Provide/authorize AWS, DNS, Omen, controlled signing workstation/key custody, and
  target-tablet access when live combined release evidence is desired.
- No repository architecture decision is open.

## Backlog additions

- Add CI-held release-signing integration only if the organization can provide
  hardware-backed or equivalently controlled key custody without exposing secrets
  to repository logs or artifacts.
