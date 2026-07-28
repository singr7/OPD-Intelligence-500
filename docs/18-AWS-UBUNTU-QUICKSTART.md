# 18 — GPU-Free AWS Deployment On A Provisioned Ubuntu Host

This is the short operator path for an already-provisioned Ubuntu EC2 instance at:

```text
https://opd-cloud.radpretation.ai
```

It uses the CLOUD1 controls already in the repository: host nginx, CPU-only Docker
Compose, full-SHA ECR images, root-only Secrets Manager material, encrypted backups,
and an initially read-only PostgreSQL writer boundary.

## 1. Preconditions

Before logging into the host, confirm:

- Ubuntu 22.04 or 24.04, `amd64` or `arm64`;
- encrypted root EBS and preferably an encrypted volume mounted at `/data`;
- an Elastic IP;
- inbound 80/443 only, plus SSM Session Manager; do not add public PostgreSQL,
  Redis, API, or web ports;
- an instance role with ECR pull, the one runtime secret read, S3 backup access,
  SSM, and CloudWatch permissions;
- private ECR repositories `opd-api`, `opd-worker`, `opd-voice-gw`, and `opd-web`;
- a versioned, encrypted, public-blocked backup bucket;
- an `A` record for `opd-cloud.radpretation.ai` pointing to the Elastic IP before
  the TLS step.

The application stack contains no CUDA, NVIDIA runtime, vLLM, Whisper, or local TTS
container. Cloud voice must be explicitly configured and tested in the Channels
console; until then, deterministic tap/text intake remains the safe fallback.

## 2. Publish one immutable release

Run on the controlled release workstation from a clean accepted commit:

```bash
cd /path/to/OPD-Intelligence-Alwar

export RELEASE_SHA="$(git rev-parse HEAD)"
export AWS_REGION="ap-south-1"
export ECR_REGISTRY="<account-id>.dkr.ecr.ap-south-1.amazonaws.com"
export NEXT_PUBLIC_API_BASE="https://opd-cloud.radpretation.ai/api"

git status --short
test -z "$(git status --porcelain)"

deploy/aws/build-release.sh "$RELEASE_SHA"
cat "releases/aws/$RELEASE_SHA.json"
```

Record `RELEASE_SHA` and the four returned image digests. Never deploy `latest`.

## 3. Install the pinned deployment checkout

Open an SSM session on the Ubuntu host. Replace the repository URL and full SHA:

```bash
export RELEASE_SHA="<full-40-character-sha>"
export REPO_URL="https://github.com/singr7/OPD-Intelligence-500.git"

sudo install -d -m 0750 -o "$USER" -g "$USER" /opt/opd/source
git clone "$REPO_URL" /opt/opd/source/repo
cd /opt/opd/source/repo
git checkout --detach "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git status --porcelain)"

sudo ln -sfn "$PWD" /opt/opd/current
sudo /opt/opd/current/deploy/aws/prepare-ubuntu.sh opd-cloud.radpretation.ai
```

The script installs Docker Compose, AWS CLI, nginx, Certbot, Git, and the stable
`/opt/opd` plus `/data` runtime layout. It does not create or print a secret.

## 4. Create and fetch the runtime secret

Create the JSON outside Git from `deploy/aws/secret-fields.example.json`. Required
identity values are:

```text
PUBLIC_HOSTNAME=opd-cloud.radpretation.ai
ENVIRONMENT_ID=aws
ENVIRONMENT_NAME=OPD Cloud standby
```

Use long independent `POSTGRES_PASSWORD`, `JWT_SECRET`, and Fernet `SECRETS_KEY`
values. Set the actual AWS region, ECR registry, and encrypted backup bucket. Vendor
credentials may be present in the secret, but a cloud profile must still pass all
three component tests before an operator publishes it.

On the host:

```bash
export AWS_REGION="ap-south-1"
export RUNTIME_SECRET_ARN="<runtime-secret-arn>"

sudo --preserve-env=AWS_REGION,RUNTIME_SECRET_ARN \
  /opt/opd/current/deploy/aws/fetch-secrets.sh

sudo stat -c '%U %G %a %n' /opt/opd/runtime/application.env
```

Expected ownership/mode is `root root 600`. Do not print the file.

## 5. Deploy as a read-only standby

`prepare-ubuntu.sh` creates `writer.env` with `OPD_WRITER_ENABLED=0`. Keep it that
way for initial deployment:

```bash
sudo /opt/opd/current/deploy/aws/deploy.sh "$RELEASE_SHA"

sudo docker compose \
  --env-file /opt/opd/runtime/application.env \
  --env-file /opt/opd/runtime/writer.env \
  -f /opt/opd/current/deploy/aws/compose.yml ps

curl -fsS http://127.0.0.1:18080/health
curl -fsS http://127.0.0.1:18080/environment | python3 -m json.tool
curl -fsS http://127.0.0.1:13000/api/health
```

The environment response must say `environment_id: aws` and return the deployed
full SHA. PostgreSQL must remain read-only:

```bash
sudo OPD_ROOT=/opt/opd/current /opt/opd/current/deploy/aws/quiesce.sh
```

## 6. Issue TLS only after DNS

Confirm DNS reaches the instance Elastic IP, then enable TLS:

```bash
getent ahostsv4 opd-cloud.radpretation.ai

sudo /opt/opd/current/deploy/aws/enable-tls.sh \
  opd-cloud.radpretation.ai ops@radpretation.ai

curl -fsS https://opd-cloud.radpretation.ai/api/health
curl -fsS https://opd-cloud.radpretation.ai/api/environment | python3 -m json.tool
curl -fsSI https://opd-cloud.radpretation.ai/
```

`enable-tls.sh` activates HSTS only after public HTTPS health and a Certbot renewal
dry run pass.

## 7. Verify the no-GPU boundary

```bash
sudo docker compose \
  --env-file /opt/opd/runtime/application.env \
  --env-file /opt/opd/runtime/writer.env \
  -f /opt/opd/current/deploy/aws/compose.yml config |
  grep -Ei 'nvidia|cuda|vllm|whisper|local[_-]?tts' && exit 1 || true

sudo ss -lntp
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Only nginx should listen publicly on 80/443. API and web bind to loopback;
PostgreSQL and Redis have no host port.

## 8. Before promotion

Do not promote an empty or stale database. First install backup operations, restore
an Omen backup, run isolated verification, and complete the single-writer drill in
`docs/17-AWS-STANDBY-RUNBOOK.md`. Promotion is a separate deliberate command; DNS
health alone never enables writes.

## References

- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Compose plugin on Linux](https://docs.docker.com/compose/install/linux/)
- [AWS CLI installation](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [Certbot usage](https://eff-certbot.readthedocs.io/en/stable/using.html)
