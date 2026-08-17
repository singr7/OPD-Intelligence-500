# 25 — Primus: the AWS cloud stack, replicated on a local CPU box

**For:** standing up `primus`, an on-prem CPU-only server, running the **same
deployment path, the same Compose file and the same runtime layout** as
`https://opd-cloud.radpretation.ai`.

This is deliberately **not** the omen path. Omen is `docker compose` in a home
directory with a GPU attached (doc 10, doc 13). Primus is doc 18's path: a pinned
detached checkout under `/opt/opd`, root-owned runtime env, CPU-only images
tagged by full SHA, `deploy.sh`, `rollback.sh`, and the read-only writer gate.

**Read [doc 18](18-AWS-UBUNTU-QUICKSTART.md) first and keep it open.** This
document does not repeat it. It records only the places where a box that is not
an EC2 instance behaves differently, and there are **six** of them.

---

## 0. Ledger

**Update this table at the end of every primus deploy**, exactly as doc 18 §0 is
updated for the cloud box. The previous SHA is `rollback.sh`'s argument.

| deployed (UTC) | release SHA | previous SHA | commits | migrations applied | notes |
|---|---|---|---|---|---|
| _(no deploy recorded yet)_ | | | | | |

---

## 1. What is identical, and therefore not described here

Every one of these is doc 18 unchanged. Do not invent a local variant:

| | |
|---|---|
| Layout | `/opt/opd/source/repo` (detached at a pinned SHA), symlinked `/opt/opd/current`; `/opt/opd/runtime/{application,writer,release}.env`; `/data/{postgres,redis,records,releases,backups}` |
| Host prep | `deploy/aws/prepare-ubuntu.sh <hostname>` — Ubuntu 22.04/24.04, amd64 or arm64 |
| Runtime env | `deploy/aws/create-local-runtime-env.sh` — generates independent Postgres/JWT/Fernet secrets, `root:root 0600` |
| Images | `deploy/aws/build-local-release.sh <sha>` — CPU-only, `opd-local/opd-{api,worker,voice-gw,web}:<full-sha>`, no `latest`, manifest written |
| Deploy | `deploy/aws/deploy.sh <sha>` — migrates, health-checks, writes `current-sha`, seeds reference data **on a writer only** |
| Compose | `deploy/aws/compose.yml` — api `18080` and web `13000` on **loopback only**, no Postgres or Redis host port, nginx the only public listener |
| Rollback | `deploy/aws/rollback.sh <previous-sha>` — refuses if the previous local image tags were pruned |
| No GPU | the stack contains no CUDA, NVIDIA runtime, vLLM, Whisper or local TTS container. Assert it with doc 18 §7's `grep -Eqi 'nvidia\|cuda\|vllm\|whisper'` |

**Terraform is not part of this.** `infra/*.tf` provisions Route 53, S3, ECR,
CloudWatch, DLM and SNS. Doc 18's path explicitly does not require ECR, and
primus requires none of the rest. Do not run terraform for this box.

---

## 2. Delta 1 — `/data` must be a real mountpoint, or nothing boots

On EC2 `/data` is an encrypted EBS volume. `boot.sh` **hard-refuses** if it is
not a mounted filesystem:

```
refusing to start: /data is not a mounted filesystem
```

That guard is load-bearing and must not be removed: without it, a boot where the
volume is missing produces an empty PostgreSQL on the root disk that looks
perfectly healthy while serving nothing. `prepare-ubuntu.sh` only *warns* about
this; `boot.sh` is the one that stops.

**Give primus a dedicated filesystem at `/data`** — a partition, a second disk,
or an LVM logical volume — and put it in `/etc/fstab` so it mounts before Docker:

```bash
sudo mkfs.ext4 /dev/<device>
echo 'UUID=<uuid> /data ext4 defaults,noatime 0 2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /data
mountpoint -q /data && echo ok        # boot.sh's exact test
```

Encrypt it (LUKS) if primus can hold anything resembling patient data — the AWS
preconditions ask for an encrypted volume and the reason does not change on-prem.

If primus genuinely has one disk, a bind mount satisfies the check
(`mount --bind /srv/opd-data /data`, plus an fstab line). It is weaker — it
cannot catch "the real storage failed to appear" — so prefer a real filesystem.

---

## 3. Delta 2 — `ENVIRONMENT_ID`, and the box that would otherwise claim to be AWS

`create-local-runtime-env.sh` hardcodes `ENVIRONMENT_ID=aws` (line 40), and
`Settings.assert_production_safe` accepts **only** `{"omen", "aws"}`
(`backend/app/config.py:349`). So a third box has no honest identity today.

Leaving it as `aws` is the wrong fix: `/environment` is what doc 18 §5 verifies
after every cloud deploy to prove *which box answered*, and two hosts both
reporting `environment_id: aws` destroys that check at exactly the moment it
matters — when you are not sure which one you are talking to.

**Do this instead.** One line in `config.py`, one in `tests/test_config.py`:

```python
if self.environment_id not in {"omen", "aws", "primus"}:
    problems.append("ENVIRONMENT_ID must be 'omen', 'aws' or 'primus'")
```

Then, after `create-local-runtime-env.sh` has run:

```bash
sudo sudoedit /opt/opd/runtime/application.env
#   ENVIRONMENT_ID=primus
#   ENVIRONMENT_NAME="OPD Primus local replica"
```

Verify it took, and that you are not looking at the cloud box:

```bash
curl -fsS http://127.0.0.1:18080/environment | python3 -m json.tool
#   environment_id: primus
```

**Leave `activate-disposable-test.sh` alone.** It refuses outside
`ENVIRONMENT_ID=aws`, and that refusal should stand — the disposable no-PHI mode
carries a world-readable kiosk PIN and OTP echo, and widening it to a third box
is a decision that deserves its own session, not a side effect of this one.

---

## 4. Delta 3 — the writer boundary: decide it explicitly, once

AWS deliberately runs as a **read-only standby** (`OPD_WRITER_ENABLED=0`).
Doc 17's whole single-writer argument is that only one box may take writes.

Two consequences, both easy to trip over:

- **`deploy.sh` seeds reference data on a writer only.** A standby primus comes
  up with a correct schema and **no hospital, no departments, no doctors** —
  which will read as a broken deploy and is not.
- A box that is read-only cannot be used to test anything that writes, which is
  most of what a local replica is for.

**Recommendation: primus is its own writer.** It replicates AWS's *configuration
and deploy mechanics*, not AWS's standby *role*.

**`promote.sh` will not do this for you, and you should not make it.** It refuses
unless `$RELEASES_DIR/restored-backup` exists — a marker written only by a
*verified restore of a real backup*. That gate is the heart of doc 17: promotion
means "this box has been proven to hold the data" and a fresh primus has been
proven to hold nothing. Weakening it to convenience a test box would remove the
check that stops the cloud box being promoted over an empty database.

Set the writer flag directly instead, which is what `deploy.sh` reads:

```bash
printf 'OPD_WRITER_ENABLED=1\n' | sudo tee /opt/opd/runtime/writer.env >/dev/null
sudo chown root:root /opt/opd/runtime/writer.env
sudo chmod 0600 /opt/opd/runtime/writer.env

sudo /opt/opd/current/deploy/aws/deploy.sh "$RELEASE_SHA"   # re-run: flips the
#   database writable, and now runs `app.seed --patients 0` for the reference data
```

Use `promote.sh` unchanged if primus is ever restored from a real backup — that
is the case it exists for.

Two rules that follow from that, and they are not optional:

1. **Primus must never point `BACKUP_BUCKET` at the cloud box's bucket.** Two
   writers syncing into one `pages/` prefix, with no `--delete` on either side,
   silently interleaves two hospitals' scanned documents. Give primus its own
   bucket or none at all (§6).
2. **Primus must never be restored from an Omen or AWS backup and then left
   writable** without completing doc 17's single-writer drill. A local replica
   holding a copy of production data *and* accepting writes is a second source
   of truth.

If instead you want primus to be a genuine warm standby, skip `promote.sh`,
accept the empty reference data, and follow doc 17 rather than this section.

---

## 5. Delta 4 — TLS is mandatory here, and neither TLS helper will work

This is the one that will cost you an afternoon if you skip it. **Plain HTTP is
not a working configuration for primus**, for two independent reasons:

1. **`build-local-release.sh` hardcodes the scheme.** Line 61:
   `--build-arg "NEXT_PUBLIC_API_BASE=https://$PUBLIC_HOSTNAME/api"`. Next
   inlines that at build time. A primus served over `http://` ships a bundle that
   calls `https://<host>/api` and **every API call fails** — with no way to fix
   it short of a rebuild.
2. **The kiosk microphone needs a secure context.** `getUserMedia` is unavailable
   over plain HTTP from anything other than `localhost`. A kiosk browser on
   another machine on the LAN gets no microphone at all.

And both shipped helpers are unusable as-is:

- **`enable-tls.sh` queries EC2 IMDS** (`169.254.169.254`) to compare the
  hostname's A record against the instance's public IPv4. On primus that curl
  fails and the script exits. It also needs Let's Encrypt to reach port 80
  inbound, which a LAN box does not offer.
- **`enable-cloudflare-tls.sh` verifies *public* HTTPS through Cloudflare**
  before enabling HSTS. There is no public path to primus, so that gate never
  passes.

### The path that works: an internal CA, and `opd-tls.conf` installed by hand

Pick a hostname primus will actually be reached by and put it in DNS or in the
kiosk machines' `/etc/hosts` — e.g. `opd-primus.radpretation.ai` with an A record
at primus's LAN IP. Public DNS pointing at a private address is fine and normal.
**That name must be `PUBLIC_HOSTNAME` before you build**, because of reason 1.

Issue a certificate from an internal CA (`mkcert` is the least painful; a
long-lived self-signed cert also works) and install its root on every kiosk and
console machine — otherwise browsers refuse, and a kiosk cannot click through a
warning:

```bash
sudo mkcert -install
sudo mkcert -cert-file /etc/ssl/opd-primus.pem \
            -key-file  /etc/ssl/opd-primus.key opd-primus.radpretation.ai
sudo chmod 0600 /etc/ssl/opd-primus.key
```

Then install doc 18's own nginx config, repointed at that certificate. This is
`enable-tls.sh`'s second half with the Certbot-specific lines replaced:

```bash
export H=opd-primus.radpretation.ai
sudo install -m 0644 /opt/opd/current/deploy/aws/nginx/opd-proxy.conf /etc/nginx/opd-proxy.conf
sudo sed "s/AWS_HOSTNAME/$H/g" /opt/opd/current/deploy/aws/nginx/opd-tls.conf \
  > /etc/nginx/sites-available/opd.conf.next

# Certbot's paths and options do not exist here; point at the internal cert.
sudo sed -i \
  -e "s#/etc/letsencrypt/live/$H/fullchain.pem#/etc/ssl/opd-primus.pem#" \
  -e "s#/etc/letsencrypt/live/$H/privkey.pem#/etc/ssl/opd-primus.key#" \
  -e '/options-ssl-nginx.conf/d' -e '/ssl_dhparam/d' \
  /etc/nginx/sites-available/opd.conf.next

# HSTS pins a broken origin into clients for a year. Not on a box you are still
# building, and not with a certificate you may reissue.
sudo sed -i '/Strict-Transport-Security/d' /etc/nginx/sites-available/opd.conf.next

sudo mv /etc/nginx/sites-available/opd.conf.next /etc/nginx/sites-available/opd.conf
sudo nginx -t && sudo systemctl reload nginx
curl -fsS "https://$H/api/health"
```

`opd-tls.conf` carries more than TLS — the OTP rate-limit zone, the WebSocket
upgrade for `/queue/ws`, the Exotel path on 18081, and the signed Android
download routes. Installing it by hand keeps all of that; hand-writing a "simple"
proxy config loses it silently.

`normalize_nginx_http2_syntax` is skipped above; if nginx ≥ 1.25.1 warns about
`listen ... http2`, apply the same rewrite the helper does.

---

## 6. Delta 5 — the three operations timers assume an AWS account

`install-operations.sh` enables **all three** timers plus the boot unit:

| unit | needs | on primus |
|---|---|---|
| `opd-boot.service` | nothing | **keep** — this is the unit that makes §2's mountpoint guard useful |
| `opd-backup.timer` | `BACKUP_BUCKET` + `AWS_REGION` (`backup.sh` hard-fails without both) | S3 credentials, or disable |
| `opd-restore-verify.timer` | the same bucket | follows the above |
| `opd-health-metrics.timer` | CloudWatch `PutMetricData` | credentials, or disable |

**Recommended:** give primus its own S3 bucket and a scoped IAM user. The backup
path is genuinely valuable — it is the only thing that carries `/data/records`,
and doc 22 notes the scripts have **never run against a real bucket on either
box**, so primus is the safest possible place to finally prove them.

If primus is to have no AWS account at all:

```bash
sudo /opt/opd/current/deploy/aws/install-operations.sh
sudo systemctl disable --now opd-backup.timer opd-restore-verify.timer opd-health-metrics.timer
systemctl list-timers --all 'opd-*'
```

and **write down that primus has no backups.** An unbacked box that looks exactly
like a backed-up one is worse than an obviously unbacked one. Re-enable the pair
the moment a bucket exists.

---

## 7. Delta 6 — matching AWS's LLM settings, which are not where you would guess

`create-local-runtime-env.sh` writes **no provider lines at all**, and prints
*"provider credentials were not added; configure and test them through the
Channels console"*. That sentence is about **channel vendors** — WhatsApp,
telephony, SMS — which live encrypted in `provider_secrets` (S-GL.1).

**The LLM is not one of them.** `llm_provider` is a plain setting
(`config.py:68`), read from `application.env` via Compose's `env_file`. So
whatever the cloud box is running was added by hand with `sudoedit`, and **no
file in this repo records it.**

### Read it off AWS; do not guess

On the cloud box — the keys only, never the values:

```bash
sudo grep -E '^(ENV|LLM_|STT_|TTS_|GEMINI_MODEL|OPENAI_MODEL|SARVAM_|MRD_|RESEARCH_|INTAKE_|OBJECT_STORE)' \
  /opt/opd/runtime/application.env | sed -E 's/(KEY|SECRET|TOKEN|PASSWORD)=.*/\1=<redacted>/'
```

Copy those lines into primus's `application.env` with `sudoedit`, substituting
primus's **own** API keys. Do not copy a key across boxes: one revoked or
rate-limited credential should not take down two environments, and per-box keys
are the only way to tell from a vendor dashboard which box spent the money.

### What you should expect to find, and what to check

- **A vision-capable `LLM_PROVIDER`.** Doc 22 §1 and the `MRD_EXTRACT_TIMEOUT_SECONDS`
  comment both describe OpenAI answering extraction calls on the AWS box in
  1.3s, so `openai` (or `gemini`) is near-certain. This matters more on primus
  than on omen: primus has **no GPU and no local model**, so a `fake` provider
  here is not degraded — it is an intake that answers itself.
- **`MRD_EXTRACT_TIMEOUT_SECONDS=60`.** At the 10s class default every extraction
  on the AWS box failed and five consecutive failures opened the circuit breaker
  while the vendor was healthy. The fix is commit `5f5e7f0`, which as of
  2026-08-17 is **on `main` but not deployed to AWS** — deploy primus at
  `5f5e7f0` or later, and bring AWS to it too.
- **`ENV`.** It gates `is_local`, which decides whether `app.seed` will plant the
  world-readable `4729` kiosk PIN. Match AWS. If primus is not local, plant a PIN
  deliberately:
  `docker compose ... exec -it api python -m scripts.set_kiosk_pin --phone <phone> --set`.
- **`KIOSK_SERVER_STT` / `KIOSK_SERVER_TTS`.** These are *build* args, not runtime
  env, and default to `1`. With no Whisper container on this stack they fall to
  the `fake` providers unless a cloud STT/TTS is configured — a canned transcript
  and silence, which looks exactly like a broken microphone. Either configure
  `STT_PROVIDER`/`TTS_PROVIDER`, or build with `KIOSK_SERVER_STT=0
  KIOSK_SERVER_TTS=0` and accept that Chrome's recogniser ships audio to Google
  (**synthetic data only — never a host that could receive PHI**).

### Ayurveda

Doc 24 §9 makes BAMS clinical sign-off a launch gate, and it has not happened.
If primus is reachable by a real patient, **close Ayurveda in the console's
Facility tab** before it is. If primus is a closed test box, note the decision in
the ledger and move on.

---

## 8. The order to actually run it in

```bash
# --- host ---------------------------------------------------------------
# §2 first: /data must be a mounted filesystem before anything else.
mountpoint -q /data || { echo "fix /data first"; exit 1; }

export RELEASE_SHA="<full-40-char-sha>"          # 5f5e7f03... or later, per §7
export REPO_URL="https://github.com/singr7/OPD-Intelligence-500.git"
export H=opd-primus.radpretation.ai              # §5: must be the real browser hostname

sudo install -d -m 0750 -o "$USER" -g "$USER" /opt/opd/source
git clone "$REPO_URL" /opt/opd/source/repo
cd /opt/opd/source/repo
git checkout --detach "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git status --porcelain)"              # build-local-release.sh refuses otherwise
sudo ln -sfn "$PWD" /opt/opd/current

sudo /opt/opd/current/deploy/aws/prepare-ubuntu.sh "$H"

# --- runtime env --------------------------------------------------------
sudo /opt/opd/current/deploy/aws/create-local-runtime-env.sh "$H" ap-south-1 [bucket]
sudo sudoedit /opt/opd/runtime/application.env
#   §3: ENVIRONMENT_ID=primus, ENVIRONMENT_NAME="OPD Primus local replica"
#   §7: the LLM/STT/MRD block copied from AWS, with primus's own keys
sudo stat -c '%U %G %a %n' /opt/opd/runtime/application.env   # root root 600

# --- build and deploy ---------------------------------------------------
sudo /opt/opd/current/deploy/aws/build-local-release.sh "$RELEASE_SHA"
sudo cat "/opt/opd/runtime/releases/$RELEASE_SHA.local-images.json"

# §4: primus is its own writer — set the flag directly; promote.sh refuses here
printf 'OPD_WRITER_ENABLED=1\n' | sudo tee /opt/opd/runtime/writer.env >/dev/null
sudo chown root:root /opt/opd/runtime/writer.env && sudo chmod 0600 /opt/opd/runtime/writer.env

sudo /opt/opd/current/deploy/aws/deploy.sh "$RELEASE_SHA"     # migrates, health-checks, seeds

# --- TLS (§5) — before any browser touches it ---------------------------
#   internal CA + opd-tls.conf, per §5

# --- operations (§6) ----------------------------------------------------
sudo /opt/opd/current/deploy/aws/install-operations.sh
#   then disable the AWS-dependent timers if primus has no account
```

## 9. Verify — and prove it is primus, not the cloud box

```bash
compose() { sudo docker compose \
  --env-file /opt/opd/runtime/application.env \
  --env-file /opt/opd/runtime/writer.env \
  --env-file /opt/opd/runtime/release.env \
  -f /opt/opd/current/deploy/aws/compose.yml "$@"; }

compose ps
curl -fsS http://127.0.0.1:18080/health
curl -fsS http://127.0.0.1:13000/api/health
curl -fsS http://127.0.0.1:18080/environment | python3 -m json.tool
#   environment_id: primus   ← §3. If this says aws, you are on the wrong box or
#                              the config change did not land. Stop.
sudo cat /opt/opd/runtime/releases/current-sha

# No GPU anywhere in the resolved config (doc 18 §7)
compose config | grep -Eqi 'nvidia|cuda|vllm|whisper|local[_-]?tts' && echo LEAK || echo clean

# Only nginx listens publicly; api/web on loopback; no Postgres or Redis port
sudo ss -lntp
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Writer state is what you chose, not what you assumed
compose exec -T postgres psql -U opd -d opd -tAc "show default_transaction_read_only"
#   off = writable (§4's recommendation);  on = standby

# MRD's shared page store (doc 22 §1) — the api writes, the worker reads
compose exec api    sh -c 'touch /data/records/.probe && ls -la /data/records'
compose exec worker sh -c 'ls -la /data/records/.probe'   # must be the same file

# Reference data actually arrived (it only does on a writer)
compose exec -T postgres psql -U opd -d opd -c "select code, name, care_system from departments order by 1;"
```

Then the only test that means anything: open `https://$H/kiosk` in a browser on
another machine, complete an intake, take a token, and see it on `https://$H/board`.
Confirm the providers that answered were the ones you configured:

```bash
compose exec -T postgres psql -U opd -d opd -c \
  "select provider, count(*) from usage_events
     where created_at > now() - interval '15 minutes' group by 1 order by 2 desc;"
```

`fake` here is a failure, not a degraded mode — this box has no local model to
fall back to.

**Finally, add the row to §0.**

---

## 10. Rollback

Identical to doc 18 §8. The previous SHA comes from §0's table:

```bash
sudo /opt/opd/current/deploy/aws/rollback.sh "$PREVIOUS_SHA"
```

It refuses if those exact local image tags are gone, so **never run
`docker image prune -a` on primus**, and keep the previous checkout until the new
release is verified. Per doc 18, `/opt/opd/current` is a symlink and this host
will accumulate more than one checkout — derive the live one, never type it:

```bash
export SRC="$(readlink -f /opt/opd/current)"
git -C "$SRC" rev-parse HEAD          # must equal current-sha
```
