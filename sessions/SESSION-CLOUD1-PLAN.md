# SESSION-CLOUD1 — GPU-Free AWS Standby

Type: execution only  
Branch: `aws-gpu-free-standby`  
Predecessor: accepted `kiosk-voice-profiles`  
Outcome: reproducible nginx + Docker AWS environment using cloud voice providers

## Start

Follow `docs/07-SESSION-PROTOCOL.md`. Read only:

1. `HANDOFF.md`
2. `STATE.md`
3. `docs/05-DEPLOYMENT.md`
4. `docs/13-OMEN-UPGRADE-RUNBOOK.md`
5. `docs/16-VOICE-CLOUD-ANDROID-EXECUTION-PLAN.md`
6. `sessions/SESSION-VOICE1.md`
7. this file

Create the branch from the accepted VOICE1 commit. Confirm the Omen remains the
current writer before provisioning or restoring anything.

## Target topology

One right-sized EC2 instance runs the CPU-only application stack:

- nginx on the host for ports 80/443;
- web, API, voice gateway, worker, beat, PostgreSQL, and Redis in Docker;
- no vLLM, local Whisper, local TTS, CUDA, NVIDIA runtime, Grafana, or demo service
  unless explicitly enabled;
- encrypted gp3 EBS data volume mounted at `/data`;
- private ECR images pinned by immutable commit SHA;
- AWS Secrets Manager for the production environment;
- CloudWatch logs/alarms and SSM Session Manager; no inbound SSH;
- S3 versioned encrypted backup bucket.

Use the existing Terraform as the base. Prefer one boring EC2/Compose box over a new
orchestrator in this session.

AWS operational references:

- [Session Manager requires no inbound SSH port](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)
- [ECR image pulls and authorization](https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-pull-ecr-image.html)
- [Secrets Manager resource policies](https://docs.aws.amazon.com/secretsmanager/latest/userguide/auth-and-access_resource-policies.html)

## Build contract

### Unit 1 — Complete infrastructure as code

Extend `infra/` to provision:

- encrypted root and data EBS volumes;
- Elastic IP and DNS inputs/outputs;
- security group with only 80/443 inbound;
- EC2 instance role with least-privilege SSM, ECR pull, one named secret read,
  CloudWatch write, and backup-bucket access;
- private ECR repositories with scan-on-push and lifecycle rules;
- versioned, encrypted S3 backup bucket with public access blocked;
- CloudWatch log groups, instance-status alarm, disk alarm, and HTTP health alarm;
- user-data that installs Docker/Compose, AWS CLI, nginx, Certbot, CloudWatch agent,
  mounts `/data`, and creates stable application directories.

Do not put secret values in Terraform state or user-data. Do not open port 22.
Terraform validation and a plan with placeholder/non-secret variables must pass.

Commit: `S CLOUD1: provision GPU-free AWS standby`

### Unit 2 — Add production Docker and nginx assets

Add a production Compose file/override that:

- uses `${ECR_REGISTRY}/opd-<service>:${IMAGE_TAG}` rather than local builds;
- omits `caddy`, local model services, and development observability;
- binds API and web only to `127.0.0.1`;
- stores PostgreSQL and Redis under `/data`;
- uses health checks, restart policies, log rotation, memory limits, and
  `stop_grace_period`;
- runs migrations as an explicit one-shot step before application replacement;
- refuses a mutable `latest` tag.

Add a complete nginx configuration that:

- redirects HTTP to HTTPS except ACME challenges;
- serves the web app at `/`;
- proxies `/api/` to the API with correct forwarded headers and timeouts;
- proxies required WebSocket paths with upgrade headers;
- serves `/downloads/` from `/data/releases/` with no directory listing;
- sets HSTS only after TLS is verified;
- applies a conservative upload limit and rate limits auth/OTP endpoints;
- exposes no PostgreSQL, Redis, API host port, metrics, or admin filesystem.

Add idempotent scripts:

- `bootstrap.sh` — first install/configuration;
- `deploy.sh <commit-sha>` — ECR login, pull, migrate, health gate, replace;
- `backup.sh` — consistent compressed encrypted Postgres dump + manifest/checksum;
- `restore.sh <backup-id>` — restore into a stopped/non-writer environment;
- `promote.sh` — explicit health/restore checks, then writer enablement;
- `rollback.sh <commit-sha>` — application rollback without data-volume deletion.

Shellcheck these scripts and make destructive targets explicit. A script must refuse
to restore over a live writer unless a separate, explicit confirmation flag is
provided.

Commit: `S CLOUD1: add nginx and immutable Compose deployment`

### Unit 3 — Build and release images

Create the reproducible image flow:

1. build API, voice gateway, worker/beat base, and web images from a clean commit;
2. produce architectures required by the chosen EC2 type and the Omen;
3. push to ECR with the full Git SHA;
4. record image digests in a release manifest;
5. deploy by digest or immutable SHA tag;
6. retain the previous known-good manifest for rollback.

Do not clone an unpinned branch on the server as the release mechanism. A Git checkout
may carry deployment assets, but running containers come from the recorded images.

Commit: `S CLOUD1: publish commit-addressed release images`

### Unit 4 — Secrets, TLS, backups, and observability

Define the AWS secret schema and an example containing field names only. It includes
database, JWT/Fernet, provider, SMS/messaging, and operational credentials. Fetch it
using the instance role into a root-owned runtime file with mode `0600`; shred or
replace transient material after Compose reads it. No secret enters logs.

Issue TLS only after DNS resolves to the Elastic IP. Configure automated renewal and
test it. Set alarms for:

- public health failure;
- instance failure;
- disk pressure;
- backup age greater than 20 minutes;
- repeated provider failures and cost-guard exhaustion.

Run backups at least every 15 minutes and a daily restore verification into an
isolated database. Record backup ID, source commit/schema revision, timestamp,
checksum, and restore result.

Commit: `S CLOUD1: secure and observe AWS standby`

### Unit 5 — Failover and failback drill

Use separate environment names and URLs, for example:

```text
Omen: https://omen.opd.radpretation.ai
AWS:  https://aws.opd.radpretation.ai
Stable/pairing alias: https://opd.radpretation.ai
```

Keep automatic DNS failover disabled in this session; it can create two writers.
Perform a timed manual drill:

1. complete a known intake on Omen and record its non-PHI identifier;
2. quiesce Omen writes and take an on-demand backup;
3. restore the backup on AWS and run schema/data integrity checks;
4. enable AWS writer and switch the stable DNS or Android pairing;
5. complete kiosk and API health checks on AWS with a cloud voice profile;
6. keep Omen read-only;
7. repeat the controlled process back to Omen.

Measure actual RPO/RTO. Verify an intake created after backup cutoff is not falsely
claimed as replicated. Close as `sessions/SESSION-CLOUD1.md`.

Commit: `S CLOUD1: prove controlled AWS failover and failback`

## Omen deployment — simple operator path

VOICE1 remains the application baseline. CLOUD1 adds backup and environment controls
without installing AWS nginx on the Omen.

```bash
git fetch origin
git switch aws-gpu-free-standby
git pull --ff-only origin aws-gpu-free-standby
docker compose build api voice-gw worker beat web
docker compose up -d --no-deps api voice-gw worker beat web
docker compose ps
curl -fsS http://127.0.0.1:18080/health
curl -fsS https://opd.radpretation.ai/api/health
```

Install only the committed Omen backup timer/service from `deploy/omen/`; point it at
the dedicated encrypted S3 prefix using least-privilege credentials or an approved
machine identity. Run one backup and restore it into a disposable database before
enabling the timer.

Rollback the application by deploying the previous image/commit. Disable the new
backup timer if it misbehaves; never delete prior backups or Docker volumes during
rollback.

## Acceptance checklist

- [ ] Terraform validate/plan is clean and contains no secret values.
- [ ] AWS has no GPU/model service and no inbound SSH.
- [ ] nginx/TLS/API/WebSocket/download paths pass from the public URL.
- [ ] Images are immutable and pullable by the EC2 role.
- [ ] Database/Redis are not public.
- [ ] Backup, isolated restore, promotion, rollback, and failback are proven.
- [ ] Actual RPO/RTO are recorded.
- [ ] Omen remains recoverable and is never a concurrent writer.
- [ ] All repository gates are green.

