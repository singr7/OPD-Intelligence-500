# 18 — GPU-Free AWS Deployment From Git On Ubuntu

This is the simple operator path for an already-provisioned Ubuntu EC2 instance at:

```text
https://opd-cloud.radpretation.ai
```

It does **not** require ECR. The host checks out one pinned Git commit, builds
CPU-only Docker images locally, tags them with the full commit SHA, and deploys
those retained images through the existing migration, health, writer, TLS, and
rollback controls.

## 0. Where the cloud box is right now

**This section is the ledger. Update it at the end of every AWS deploy** — it is
the only place that records what `https://opd-cloud.radpretation.ai` is actually
running, and the previous SHA is what `rollback.sh` needs.

| deployed (UTC) | release SHA | previous SHA | commits | migrations applied | notes |
|---|---|---|---|---|---|
| 2026-08-10 | `8fd588a81bb4f0e5612a8bbe476d93f91c2dad0a` | `036b6f313226b100176dfe777ad21b07fc32b1f6` | 4 | none | Field fixes off the first ayurveda deploy: the Urdu-script guard extended to the doctor's transcript, the boarding pass printing on one sheet instead of three, an unheard clip reporting itself, and the three `NEXT_PUBLIC_PASS_*` / `PRINT_BRIDGE_URL` build args that were unsettable in every image ever built. |
| 2026-08-10 | `036b6f313226b100176dfe777ad21b07fc32b1f6` | `3e5dd8f9f872a803d5ef412ec54a6272787d65c5` | 68 | 6 | SESSION-AYUR-2. First cloud deploy carrying the ayurveda module (docs/24), MRD, clinical notes, the research assistant and the allergy log. |

Newest first. The six migrations in the `036b6f31` jump — `efb79a43afb3`, `02571a5c1871`, `9f2ab41c77d3`,
`8ef31aa60c55`, `4ce8cb36a165`, `28e0ff23658b` — are all additive, with server
defaults and no backfill. `deploy.sh` applies them itself; there is no separate
migration step on this path. `8fd588a` is code only.

If that kiosk has a printer attached, the three build args wired in `8fd588a`
have to be exported **before** `build-local-release.sh` — Next inlines
`NEXT_PUBLIC_*` at build time, so they cannot be set afterwards:
`PRINT_BRIDGE_URL`, `PASS_AUTOPRINT=1`, and `PASS_GEOMETRY=roll58` for a 58mm
roll.

Whatever this table says, the box is the authority. Confirm before you deploy:

```bash
sudo cat /opt/opd/runtime/releases/current-sha
curl -fsS http://127.0.0.1:18080/environment | python3 -m json.tool
```

### Which checkout is live — read the symlink, never assume the path

`/opt/opd/current` is a **symlink to a checkout**, and the host has accumulated
more than one: `/opt/opd/source/repo` and `/opt/opd/source/repo-new` both exist,
at different commits. Every other document in this repo hard-codes
`/opt/opd/source/repo`, and on 2026-08-10 that was the **stale** one — the live
checkout was `repo-new`. Running `git fetch` in the wrong directory produces
`fatal: Invalid revision range`, which looks like a missing commit and is not.

So derive the path instead of typing it:

```bash
export SRC="$(readlink -f /opt/opd/current)"
git -C "$SRC" rev-parse HEAD                       # must equal current-sha
git -C "$SRC" remote get-url origin                # singr7/OPD-Intelligence-500.git
```

`$SRC` is then the only directory you touch. If `rev-parse HEAD` does **not**
match `current-sha`, stop: the symlink and the deployed images disagree, and
`rollback.sh` will be reading scripts from a commit that was never deployed.

Two related traps on this host:

- **Do not delete the other checkout.** It is the rollback checkout until the new
  release is verified. Likewise never `docker image prune -a` here — `rollback.sh`
  refuses if the previous release's local image tags are gone.
- **The origin is `OPD-Intelligence-500.git`**, even though the working copy on a
  developer machine may be a directory named `OPD-Intelligence-Alwar`. The remote
  on the box is correct as-is; do not "fix" it.

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
  the TLS step. It may be DNS-only for Certbot or Cloudflare-proxied when using a
  supplied origin certificate.

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
  --env-file /opt/opd/runtime/release.env \
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

## 6. Enable TLS after DNS points to the host

Choose exactly one of the following paths.

### Direct DNS with Let's Encrypt

```bash
getent ahostsv4 opd-cloud.radpretation.ai

sudo /opt/opd/current/deploy/aws/enable-tls.sh \
  opd-cloud.radpretation.ai ops@radpretation.ai

curl -fsS https://opd-cloud.radpretation.ai/api/health
curl -fsS https://opd-cloud.radpretation.ai/api/environment | python3 -m json.tool
curl -fsSI https://opd-cloud.radpretation.ai/
```

HSTS is enabled only after HTTPS health and a Certbot renewal dry run pass.

### Cloudflare proxy with a supplied origin certificate

In Cloudflare:

1. Point the proxied (orange-cloud) `A` record at the Elastic IP.
2. Prefer **SSL/TLS encryption mode: Full (strict)**. If the available origin
   certificate does not match this hostname, use **Full** temporarily. Never use
   Flexible with this nginx configuration.

Place a hostname-matching, unexpired certificate and its matching private key on
the host. A Cloudflare Origin CA certificate is valid for this path, although it
will not be trusted by browsers connecting directly to the origin. For a public
CA certificate, the PEM must contain the complete served certificate chain.

Inspect the certificate without displaying the key:

```bash
sudo openssl x509 -in /etc/ssl/opd.pem \
  -noout -subject -issuer -dates -ext subjectAltName

sudo /opt/opd/current/deploy/aws/enable-cloudflare-tls.sh \
  opd-cloud.radpretation.ai /etc/ssl/opd.pem /etc/ssl/opd.key

curl -fsS https://opd-cloud.radpretation.ai/api/health
curl -fsSI https://opd-cloud.radpretation.ai/
```

The helper checks expiry, hostname coverage, and certificate/key matching. It
downloads Cloudflare's current published proxy networks and configures nginx to
honor `CF-Connecting-IP` only from those trusted networks. It verifies origin
HTTPS locally, then verifies public HTTPS through Cloudflare before enabling
HSTS. Supplied certificates are not renewed by Certbot; monitor their expiry.

The optional fourth argument explicitly selects non-strict Full mode:

```bash
sudo /opt/opd/current/deploy/aws/enable-cloudflare-tls.sh \
  opd-cloud.radpretation.ai /etc/ssl/opd.pem /etc/ssl/opd.key full
```

Full mode still encrypts traffic between Cloudflare and nginx, but Cloudflare
does not authenticate the origin certificate or requested hostname. Use this
only as an explicit compatibility choice. Omit `full` (or pass `strict`) after
installing a certificate whose SAN covers `opd-cloud.radpretation.ai`.

After verification, restrict origin security-group ingress on 80/443 to
Cloudflare's current published IPv4 and IPv6 ranges if the origin is intended to
be proxy-only. Keep those allowlists synchronized when Cloudflare changes them.

## 7. Verify the no-GPU and private-port boundaries

```bash
sudo docker compose \
  --env-file /opt/opd/runtime/application.env \
  --env-file /opt/opd/runtime/writer.env \
  --env-file /opt/opd/runtime/release.env \
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

## Disposable no-PHI regression test

Before a production-like promotion, the isolated AWS database may be made writable
for synthetic regression testing. This mode must not receive PHI, must not use the
stable production alias, and refuses to start over a verified restored database:

```bash
sudo /opt/opd/current/deploy/aws/activate-disposable-test.sh \
  <full-release-sha> --confirm-no-phi
```

Activation also selects the fake SMS provider, enables OTP echo only in the test
container environment, and seeds the synthetic Android demo patient
`+915551900001`. A demo Android build displays and prefills the returned OTP.

After testing, erase the disposable PostgreSQL database and Redis state, recreate
the schema, and return AWS to read-only standby:

```bash
sudo /opt/opd/current/deploy/aws/end-disposable-test.sh \
  <full-release-sha> --confirm-delete-test-data
```

The cleanup command is deliberately destructive and runs only when its disposable
marker exists. Complete it before restoring an Omen backup or starting the formal
promotion procedure.

## References

- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Compose plugin on Linux](https://docs.docker.com/compose/install/linux/)
- [Certbot usage](https://eff-certbot.readthedocs.io/en/stable/using.html)
