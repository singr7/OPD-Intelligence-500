# 18 — GPU-Free AWS Deployment From Git On Ubuntu

This is the simple operator path for an already-provisioned Ubuntu EC2 instance at:

```text
https://opd-cloud.radpretation.ai
```

It does **not** require ECR. The host checks out one pinned Git commit, builds
CPU-only Docker images locally, tags them with the full commit SHA, and deploys
those retained images through the existing migration, health, writer, TLS, and
rollback controls.

## 1. Preconditions

Confirm:

- Ubuntu 22.04 or 24.04, `amd64` or `arm64`;
- at least 4 vCPU, 8 GB RAM, and enough disk for two retained application releases;
- encrypted root EBS and preferably an encrypted volume mounted at `/data`;
- an Elastic IP;
- inbound 80/443 only, plus SSM Session Manager or tightly restricted temporary
  SSH access;
- no public PostgreSQL, Redis, API, or web ports;
- a Git credential/deploy key if the repository is private;
- an `A` record for `opd-cloud.radpretation.ai` pointing to the Elastic IP before
  the TLS step.

The Compose stack contains no CUDA, NVIDIA runtime, vLLM, Whisper, or local TTS
container. Cloud voice must be configured and tested through the Channels console;
until then, deterministic tap/text intake is the safe fallback.

## 2. Install one pinned checkout

Open a shell on the Ubuntu host. Replace the repository URL and SHA:

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

The preparation script installs Docker Engine/Compose, AWS CLI, nginx, Certbot,
Git, and the stable `/opt/opd` plus `/data` runtime layout. It does not create or
print an application secret.

## 3. Create the root-only runtime environment

Generate independent database, JWT, and Fernet secrets directly on the host:

```bash
sudo /opt/opd/current/deploy/aws/create-local-runtime-env.sh \
  opd-cloud.radpretation.ai ap-south-1

sudo stat -c '%U %G %a %n' /opt/opd/runtime/application.env
```

Expected ownership/mode is:

```text
root root 600 /opt/opd/runtime/application.env
```

The generated file selects:

```text
ECR_REGISTRY=opd-local
OPD_IMAGE_SOURCE=local
ENVIRONMENT_ID=aws
PUBLIC_HOSTNAME=opd-cloud.radpretation.ai
```

`ECR_REGISTRY` is only the existing Compose image-prefix variable here; `opd-local`
is a local Docker namespace and does not contact a registry.

If an encrypted S3 backup bucket already exists, use this command instead of the
one above and pass its name as the third argument. Otherwise leave it blank for
initial installation and configure backups before any promotion:

```bash
sudo /opt/opd/current/deploy/aws/create-local-runtime-env.sh \
  opd-cloud.radpretation.ai ap-south-1 <backup-bucket>
```

The creator refuses to overwrite an existing runtime file. Use `sudoedit
/opt/opd/runtime/application.env` for deliberate later changes; never print the
file or place it in Git.

## 4. Build the CPU-only release locally

Build from the clean pinned checkout:

```bash
cd /opt/opd/current
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git status --porcelain)"

sudo /opt/opd/current/deploy/aws/build-local-release.sh "$RELEASE_SHA"

sudo cat \
  "/opt/opd/runtime/releases/$RELEASE_SHA.local-images.json"
```

The build produces:

```text
opd-local/opd-api:<full-sha>
opd-local/opd-worker:<full-sha>
opd-local/opd-voice-gw:<full-sha>
opd-local/opd-web:<full-sha>
```

It records the four immutable local image IDs in the manifest and never creates a
`latest` tag.

## 5. Deploy as a read-only standby

Host preparation creates `writer.env` with `OPD_WRITER_ENABLED=0`. Keep it
read-only during initial deployment:

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
full SHA. Verify PostgreSQL remains read-only:

```bash
sudo /opt/opd/current/deploy/aws/quiesce.sh
```

## 6. Issue TLS after DNS points to the host

```bash
getent ahostsv4 opd-cloud.radpretation.ai

sudo /opt/opd/current/deploy/aws/enable-tls.sh \
  opd-cloud.radpretation.ai ops@radpretation.ai

curl -fsS https://opd-cloud.radpretation.ai/api/health
curl -fsS https://opd-cloud.radpretation.ai/api/environment | python3 -m json.tool
curl -fsSI https://opd-cloud.radpretation.ai/
```

HSTS is enabled only after HTTPS health and a Certbot renewal dry run pass.

## 7. Verify the no-GPU and private-port boundaries

```bash
sudo docker compose \
  --env-file /opt/opd/runtime/application.env \
  --env-file /opt/opd/runtime/writer.env \
  -f /opt/opd/current/deploy/aws/compose.yml config |
  grep -Eqi 'nvidia|cuda|vllm|whisper|local[_-]?tts' && exit 1 || true

sudo ss -lntp
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Only nginx should listen publicly on 80/443. API and web bind to loopback;
PostgreSQL and Redis have no host port.

## 8. Roll back to a retained local release

Keep the previous local images and its deployment checkout. To roll application
code back without changing data or writer state:

```bash
export PREVIOUS_SHA="<retained-full-sha>"
sudo /opt/opd/current/deploy/aws/rollback.sh "$PREVIOUS_SHA"
```

Rollback refuses if those exact local image tags are no longer present. Do not run
`docker image prune -a` on this host.

## 9. Before promotion

Do not promote an empty or stale database. First configure encrypted backups,
restore an Omen backup, run isolated verification, and complete the single-writer
drill in `docs/17-AWS-STANDBY-RUNBOOK.md`. Promotion is separate and deliberate;
DNS health alone never enables writes.

## References

- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Compose plugin on Linux](https://docs.docker.com/compose/install/linux/)
- [Certbot usage](https://eff-certbot.readthedocs.io/en/stable/using.html)
