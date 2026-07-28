# HANDOFF — after SESSION-CLOUD1

**Repo state:** branch `aws-gpu-free-standby`; final close commit is the branch tip.
Focused Terraform, Compose, secret/drill, ShellCheck, writer-lock, and secret-scan
gates are green. The pre-existing `web/tsconfig.tsbuildinfo` modification remains
intentionally uncommitted. The full application suite was not repeated at the
user's direction; VOICE1 had just closed it green.

CLOUD1's repository build is complete: encrypted/no-SSH one-box Terraform,
immutable multi-arch ECR releases, CPU-only Compose behind host nginx, root-only
Secrets Manager runtime material, 15-minute encrypted backups, daily isolated
restore verification, alarms, explicit read-only demotion/promotion, and manual
failover/failback evidence tooling.

## External CLOUD1 release gate

Nothing was provisioned from this environment. Before calling the standby live:

1. Supply an AWS account/credentials, approved AMI, runtime secret ARN, DNS, and
   alarm email outside Git; review and apply the real Terraform plan.
2. Build/push the branch's exact full SHA and retain the returned digest manifest.
3. Boot through SSM, fetch secrets, deploy, issue TLS after DNS, and install timers.
4. Pass public nginx/API/WebSocket/download and cloud-voice kiosk checks.
5. Install/test the Omen backup timer with one disposable restore.
6. Run Omen→AWS→Omen with no concurrent writer; finalize the non-PHI drill record.
7. Record actual RPO/RTO and prove a post-cutoff intake was not claimed as copied.

## Next session — SESSION-ANDROID1

The user authorized proceeding despite recorded external gates. Create
`android-pairing-release` from this exact close commit and execute
`sessions/SESSION-ANDROID1-PLAN.md`. Preserve the distinction between locally
built distribution controls and unavailable live Omen/AWS/tablet evidence.

Start with:

```bash
git status --short
git log -1 --oneline
sed -n '1,260p' sessions/SESSION-ANDROID1-PLAN.md
```

## Watch out for

- `OPD_WRITER_ENABLED` is an operator mirror; PostgreSQL
  `default_transaction_read_only` is the enforced writer boundary.
- `deploy.sh` deliberately stops app services while temporarily making migrations
  writable, then restores the prior writer mode before replacement.
- `enable-tls.sh` activates HSTS only after HTTPS health passes.
- AWS application images are full-SHA tags; digest manifests retain current and
  previous. Never introduce `latest`.
- Secret example values are blank by design; real values belong only in the one
  Secrets Manager object.

## Decisions needed from the human

- Provide/authorize AWS, DNS, Omen, and tablet access when live release evidence
  is desired. No architecture decision is open.

## Backlog additions

- Consider KMS CMKs for backup/ECR encryption if the pilot's compliance review
  requires customer-managed keys instead of AWS-managed AES-256.
