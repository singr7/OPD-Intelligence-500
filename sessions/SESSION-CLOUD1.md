# SESSION-CLOUD1 — GPU-Free AWS Standby

**Date:** 2026-07-28 · **Scope ref:** `sessions/SESSION-CLOUD1-PLAN.md`

## Acceptance criteria checklist

- [x] Terraform validates and a credential-free mocked plan passes with placeholder,
  non-secret inputs.
- [x] IaC has encrypted root/data volumes, no port 22, least-privilege instance
  access, immutable private ECR repositories, versioned/encrypted backups, logs,
  alarms, EIP, and optional DNS.
- [x] The production Compose contract contains only PostgreSQL, Redis, API,
  voice-gw, worker, beat, and web; internal services are private and application
  host ports bind only to loopback.
- [x] nginx assets cover TLS redirect, ACME, API, WebSockets, downloads, upload
  limits, forwarded headers, OTP rate limits, and post-verification HSTS.
- [x] Immutable deploy, migration, rollback, encrypted backup, guarded restore,
  writer promotion/quiesce, secrets fetch, TLS, and operational timer scripts
  pass focused contract and ShellCheck gates.
- [x] The image release flow builds amd64+arm64 from a clean full Git SHA, pushes
  immutable tags, reads ECR digests back, and retains the prior manifest.
- [x] A disposable PostgreSQL proof showed demotion rejects writes and explicit
  promotion restores them.
- [ ] Real Terraform apply/ECR push/SSM boot/TLS public path: not run because this
  session has no AWS account credentials or DNS authority.
- [ ] Omen backup, AWS restore, promotion, public cloud-voice intake, failback,
  and measured RPO/RTO: not run because neither live environment is accessible.
- [ ] Full application suite: intentionally not repeated at the user's direction;
  VOICE1 had just closed it green. The attempted baseline was stopped during the
  web image build before changing code.

## What was built

- `infra/` now provisions the boring one-box standby: encrypted gp3 storage,
  HTTP/HTTPS-only networking, SSM, narrowly scoped secret/ECR/S3/CloudWatch
  permissions, four immutable ECR repos, backup versioning, log groups, and six
  operational alarms.
- `deploy/aws/compose.yml` is a standalone CPU-only production stack. It contains
  no Caddy, GPU/model, Grafana, Loki, demo, or publicly exposed data service.
- nginx has separate pre-TLS and verified-TLS configurations. HSTS is activated
  only after certificate issuance and a successful HTTPS API health check.
- The database role's `default_transaction_read_only` is the writer lock.
  Deploy preserves it, restore refuses a live writer by default, promotion
  requires a verified-restore marker, and rollback never touches data/writer state.
- Secrets Manager JSON is converted through a strict field allow-list into a
  root-owned `0600` file; transient JSON is shredded and values are never logged.
- Backups run every 15 minutes with SHA-256/source commit/schema metadata; a daily
  isolated restore updates the versioned manifest with its verification result.
- `build-release.sh` builds and pushes commit-addressed multi-architecture images
  and records digest manifests with current/previous known-good retention.
- The Omen backup timer, controlled handoff runbook, non-PHI drill template, and
  RPO/RTO report validator complete the operator path.

## Decisions made

- Database-enforced read-only state, not DNS or a flag file alone, is the
  concurrency boundary. Services restart after demotion so pooled connections
  cannot retain prior write capability.
- Automatic DNS failover remains disabled. Promotion is a deliberate restore,
  integrity check, writer enablement, and alias/pairing change.
- The app images target both `linux/arm64` (t4g AWS) and `linux/amd64` (Omen).
- Live evidence is an external release gate. Local scripts/tests are not presented
  as proof that AWS, DNS, TLS renewal, ECR, S3, or Omen worked.

## Deviations from spec

- The planned Unit 5 commit wording said “prove” failover/failback. The commit says
  “add” because only the writer lock could be proved locally; public RPO/RTO evidence
  would otherwise be fabricated.
- The user explicitly accepted the still-open VOICE1 external gate as CLOUD1's
  predecessor and later waived repeating the just-completed full suite.

## Tests & evidence

- `deploy/aws/test-contract.sh`: Compose render/negative-service/private-port,
  timer, secret converter (**4**), and drill report (**4**) tests green.
- `deploy/aws/test-writer-lock.sh`: disposable PostgreSQL 16 demote/write-refusal/
  promote/write proof green.
- Terraform 1.9.8: recursive format check, validate, and mocked plan (**1**) green.
- ShellCheck 0.10.0: all AWS/Omen/bootstrap shell scripts green.
- Secret-pattern scan and `git diff --check`: green.
- `make test`: not repeated by explicit user instruction; last full result is the
  VOICE1 close (backend 1,261; voice-gw 25; web 48; Android green).

## Known gaps / stubs introduced

- No AWS resource exists from this session until an operator supplies an account,
  real AMI, secret ARN, DNS/email inputs, reviews the plan, and applies it.
- No image was pushed and no release digest was obtained from real ECR.
- TLS issuance/renewal, public nginx/API/WebSocket/download paths, S3 backup, and
  daily restore verification are implemented but not live-proven.
- The failover report remains blank; actual RPO/RTO and the post-cutoff exclusion
  must be recorded during the two-environment drill.

## Commits

- `cbcc4cd` — S CLOUD1: provision GPU-free AWS standby
- `5cb9d64` — S CLOUD1: add nginx and immutable Compose deployment
- `18e987d` — S CLOUD1: publish commit-addressed release images
- `080ae9e` — S CLOUD1: secure and observe AWS standby
- `b152d6f` — S CLOUD1: add controlled failover and failback drill
- final close commit — S CLOUD1: session close — standby controls locally green

---
