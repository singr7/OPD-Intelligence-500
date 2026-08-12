#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEST_RUNTIME="$(mktemp -d)"
trap 'rm -rf "$TEST_RUNTIME"' EXIT

printf 'POSTGRES_PASSWORD=test-only\n' >"$TEST_RUNTIME/application.env"
printf 'OPD_WRITER_ENABLED=0\n' >"$TEST_RUNTIME/writer.env"
printf 'RELEASE_SHA=0123456789abcdef0123456789abcdef01234567\n' >"$TEST_RUNTIME/release.env"

CONFIG="$TEST_RUNTIME/compose.rendered.yml"
OPD_RUNTIME="$TEST_RUNTIME" \
ECR_REGISTRY="000000000000.dkr.ecr.ap-south-1.amazonaws.com" \
IMAGE_TAG="0123456789abcdef0123456789abcdef01234567" \
POSTGRES_PASSWORD="test-only" \
docker compose -f "$SCRIPT_DIR/compose.yml" config >"$CONFIG"

if grep -Eq '(^|[[:space:]])(caddy|grafana|loki|vllm|whisper|local-tts):' "$CONFIG"; then
  echo "GPU, local-model, edge, or development observability service leaked into AWS Compose" >&2
  exit 1
fi
if grep -Eq 'published: "(5432|6379|8000|8080|3000)"' "$CONFIG"; then
  echo "a private database/cache/application port is publicly published" >&2
  exit 1
fi
grep -q 'host_ip: 127.0.0.1' "$CONFIG"
grep -q 'opd-api:0123456789abcdef0123456789abcdef01234567' "$CONFIG"
grep -q '/data/postgres' "$CONFIG"
grep -q '/data/redis' "$CONFIG"
# Scanned pages must reach the worker too, not just the api: extraction runs in
# a different container from the upload (doc 22 §1).
grep -q '/data/records' "$CONFIG"

for script in "$SCRIPT_DIR"/*.sh "$REPO_ROOT/infra/user_data.sh"; do
  bash -n "$script"
done

grep -q 'linux/amd64,linux/arm64' "$SCRIPT_DIR/build-release.sh"
grep -q 'git status --porcelain' "$SCRIPT_DIR/build-release.sh"
grep -q 'status --porcelain' "$SCRIPT_DIR/build-local-release.sh"
grep -q 'opd-local' "$SCRIPT_DIR/build-local-release.sh"
grep -q 'prepare_release_images' "$SCRIPT_DIR/deploy.sh"
grep -q 'write_release_env' "$SCRIPT_DIR/rollback.sh"
grep -q 'IMAGE_TAG=%s' "$SCRIPT_DIR/lib.sh"
if grep -qE '(^|:)latest([[:space:]]|$)' "$SCRIPT_DIR/compose.yml"; then
  echo "mutable latest tag leaked into AWS Compose" >&2
  exit 1
fi

LOCAL_CONFIG="$TEST_RUNTIME/compose.local.rendered.yml"
OPD_RUNTIME="$TEST_RUNTIME" \
ECR_REGISTRY="opd-local" \
IMAGE_TAG="0123456789abcdef0123456789abcdef01234567" \
POSTGRES_PASSWORD="test-only" \
docker compose -f "$SCRIPT_DIR/compose.yml" config >"$LOCAL_CONFIG"
grep -q 'opd-local/opd-api:0123456789abcdef0123456789abcdef01234567' "$LOCAL_CONFIG"

grep -q 'Strict-Transport-Security' "$SCRIPT_DIR/nginx/opd-tls.conf"
grep -q 'Strict-Transport-Security' "$SCRIPT_DIR/nginx/opd-cloudflare-tls.conf"
if grep -q 'Strict-Transport-Security' "$SCRIPT_DIR/nginx/opd-http.conf"; then
  echo "HSTS must not be enabled before TLS verification" >&2
  exit 1
fi
grep -q 'x509.*-checkhost' "$SCRIPT_DIR/enable-cloudflare-tls.sh"
grep -q 'CLOUDFLARE_MODE.*strict' "$SCRIPT_DIR/enable-cloudflare-tls.sh"
grep -q 'full|strict' "$SCRIPT_DIR/enable-cloudflare-tls.sh"
grep -q 'CF-Connecting-IP' "$SCRIPT_DIR/configure-cloudflare-real-ip.sh"
grep -q 'www.cloudflare.com/ips-v4' "$SCRIPT_DIR/configure-cloudflare-real-ip.sh"
grep -q 'curl -kfsS' "$SCRIPT_DIR/enable-cloudflare-tls.sh"
grep -q 'normalize_nginx_http2_syntax' "$SCRIPT_DIR/enable-cloudflare-tls.sh"
if grep -q 'proxy_read_timeout' "$SCRIPT_DIR/nginx/opd-proxy.conf"; then
  echo "shared proxy config must not duplicate per-location read timeouts" >&2
  exit 1
fi
grep -q -- '--confirm-no-phi' "$SCRIPT_DIR/activate-disposable-test.sh"
grep -q 'restored-backup' "$SCRIPT_DIR/activate-disposable-test.sh"
grep -q 'OTP_DEBUG_ECHO=true' "$SCRIPT_DIR/activate-disposable-test.sh"
grep -q 'python -m scripts.seed_app_demo' "$SCRIPT_DIR/activate-disposable-test.sh"
grep -q 'refreshing existing disposable test mode' \
  "$SCRIPT_DIR/activate-disposable-test.sh"
grep -q 'get("debug_code")' "$SCRIPT_DIR/activate-disposable-test.sh"
grep -q -- '--force-recreate api voice-gw worker beat web' \
  "$SCRIPT_DIR/activate-disposable-test.sh"
grep -q -- '--confirm-delete-test-data' "$SCRIPT_DIR/end-disposable-test.sh"
grep -q 'disposable-test-active' "$SCRIPT_DIR/end-disposable-test.sh"
grep -q 'FLUSHALL' "$SCRIPT_DIR/end-disposable-test.sh"
grep -q 'OnCalendar=\*:0/15' "$SCRIPT_DIR/systemd/opd-backup.timer"
grep -q 'OnCalendar=\*-\*-\* 04:15:00' "$SCRIPT_DIR/systemd/opd-restore-verify.timer"
python3 "$SCRIPT_DIR/test_secret_to_env.py"
python3 "$SCRIPT_DIR/test_drill_report.py"

# --- scanned pages are part of the backup (doc 22 §2) -----------------------
# Both backup scripts must sync the pages, or a restore brings back readings
# whose photographs are gone.
grep -q 's3 sync' "$SCRIPT_DIR/backup.sh"
grep -q 's3 sync' "$REPO_ROOT/deploy/omen/cloud-backup.sh"
# …and restore must bring them back, which is the half that is easy to forget.
grep -q 's3 sync' "$SCRIPT_DIR/restore.sh"
# The daily drill must prove the pages are really there, not just that the
# database restored. Without this check a backup containing no scanned reports
# at all still reports "verified".
grep -q 'head-object' "$SCRIPT_DIR/verify-restore.sh"

# **Dump before sync.** Pages are append-only, so a sync taken after the dump
# necessarily contains every page the dump references. The reverse order drops
# precisely the report scanned during the backup. Asserted by line order,
# because a comment saying so is not a test.
for script in "$SCRIPT_DIR/backup.sh" "$REPO_ROOT/deploy/omen/cloud-backup.sh"; do
  dump_line="$(grep -n 'pg_dump' "$script" | head -n1 | cut -d: -f1)"
  sync_line="$(grep -n 's3 sync' "$script" | head -n1 | cut -d: -f1)"
  if [[ -z "$dump_line" || -z "$sync_line" || "$dump_line" -ge "$sync_line" ]]; then
    echo "$script must dump the database before syncing pages (doc 22 §2)" >&2
    exit 1
  fi
done

# No --delete on either side: a restore of an older database must still find its
# pages, and nothing is entitled to remove a scanned report as a sync artefact.
if grep -E 's3 sync' "$SCRIPT_DIR/backup.sh" "$SCRIPT_DIR/restore.sh" \
  "$REPO_ROOT/deploy/omen/cloud-backup.sh" | grep -q -- '--delete'; then
  echo "page sync must never use --delete (doc 22 §2)" >&2
  exit 1
fi
grep -q 'OnCalendar=\*:0/15' "$REPO_ROOT/deploy/omen/opd-cloud-backup.timer"
grep -q 'application/vnd.android.package-archive' "$SCRIPT_DIR/nginx/opd-tls.conf"
grep -q 'max-age=31536000, immutable' "$SCRIPT_DIR/nginx/opd-tls.conf"
grep -q 'opd-cloud.radpretation.ai' "$REPO_ROOT/docs/18-AWS-UBUNTU-QUICKSTART.md"
if grep -Eqi 'nvidia|cuda|vllm|whisper|local[_-]?tts' "$SCRIPT_DIR/compose.yml"; then
  echo "GPU or local-model reference leaked into AWS Compose" >&2
  exit 1
fi

# --- reference data reaches the box on every deploy --------------------------
# A migration adds a *column*; only the seed adds a *row*. Doc 24's AYUR
# department shipped in 8fd588a and never appeared in the console, because this
# path applied `4ce8cb36a165` and then had no step that could load the
# department the column exists for. Nothing errored — which is why it needs a
# test rather than a docs line.
grep -q 'python -m app.seed' "$SCRIPT_DIR/deploy.sh"

# `--patients 0` is load-bearing on a live box: the seed defaults to generating
# 50 fake patients, and patients are deliberately exempt from its
# never-overwrite rule (app/seed.py).
grep -q 'app.seed --patients 0' "$SCRIPT_DIR/deploy.sh"

# Publishing a tree asserts a clinical review happened. A deploy script is not
# entitled to make that claim (doc 03 §3).
if grep -q -- '--publish-trees' "$SCRIPT_DIR/deploy.sh"; then
  echo "deploy.sh must not publish trees: publishing is a clinical act (doc 03 §3)" >&2
  exit 1
fi

seed_line="$(grep -n 'python -m app.seed' "$SCRIPT_DIR/deploy.sh" | head -n1 | cut -d: -f1)"
sha_line="$(grep -n 'current-sha' "$SCRIPT_DIR/deploy.sh" | tail -n1 | cut -d: -f1)"

# Writer boxes only. Seeding a standby would make it a second writer, which
# doc 17 forbids outright — the two databases are never simultaneous writers.
if ! sed -n "$((seed_line - 1))p" "$SCRIPT_DIR/deploy.sh" | grep -q 'OPD_WRITER_ENABLED'; then
  echo "deploy.sh must seed only when this box is the writer (doc 17)" >&2
  exit 1
fi

# The release is recorded *before* the seed runs, so a failed seed costs
# reference data and an exit code — never an outage, and never a set of
# deployed images that `current-sha` and `rollback.sh` cannot account for.
if [[ -z "$sha_line" || -z "$seed_line" || "$sha_line" -ge "$seed_line" ]]; then
  echo "deploy.sh must record current-sha before seeding" >&2
  exit 1
fi

echo "AWS deployment contract: ok"
