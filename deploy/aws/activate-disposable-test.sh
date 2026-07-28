#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
require_sha "${1:-}"
if [[ "${2:-}" != "--confirm-no-phi" ]]; then
  echo "usage: activate-disposable-test.sh <full-release-sha> --confirm-no-phi" >&2
  exit 2
fi
REQUESTED_SHA="$1"
load_env
IMAGE_TAG="$REQUESTED_SHA"
export IMAGE_TAG

if [[ "${ENVIRONMENT_ID:-}" != "aws" ]]; then
  echo "refusing disposable mode outside ENVIRONMENT_ID=aws" >&2
  exit 3
fi
if [[ -s "$RELEASES_DIR/restored-backup" ]]; then
  echo "refusing disposable mode: this database has a verified restore marker" >&2
  exit 3
fi
if [[ -e "$RELEASES_DIR/disposable-test-active" ]]; then
  echo "refusing: disposable test mode is already marked active" >&2
  exit 3
fi

prepare_release_images
install -d -m 0750 "$RELEASES_DIR"
umask 027
{
  printf 'mode=disposable-no-phi\n'
  printf 'release_sha=%s\n' "$IMAGE_TAG"
  printf 'activated_at=%s\n' "$(date -u +%FT%TZ)"
} >"$RELEASES_DIR/disposable-test-active"

rollback_on_error() {
  local status=$?
  trap - ERR
  echo "activation failed; returning AWS to read-only standby" >&2
  compose stop api voice-gw worker beat web >/dev/null 2>&1 || true
  set_database_read_only on >/dev/null 2>&1 || true
  write_writer_env 0 || true
  compose up -d --wait postgres redis >/dev/null 2>&1 || true
  compose up -d --wait --force-recreate api web >/dev/null 2>&1 || true
  rm -f "$RELEASES_DIR/disposable-test-active"
  exit "$status"
}
trap rollback_on_error ERR

compose stop api voice-gw worker beat web
set_database_read_only off
write_writer_env 1
{
  printf 'ENV=test\n'
  printf 'OTP_DEBUG_ECHO=true\n'
  printf 'OTP_RESEND_COOLDOWN_SECONDS=0\n'
  printf 'SMS_PROVIDER=fake\n'
} >>"$WRITER_ENV"
chmod 0600 "$WRITER_ENV"
compose up -d --wait postgres redis
compose up -d --wait --force-recreate api voice-gw worker beat web
curl -fsS http://127.0.0.1:18080/health >/dev/null
curl -fsS "https://${PUBLIC_HOSTNAME:?set PUBLIC_HOSTNAME}/api/health" >/dev/null
compose exec -T api python -m app.seed
compose exec -T api python -m scripts.seed_app_demo

trap - ERR
echo "DISPOSABLE AWS TEST MODE ACTIVE"
echo "Demo patient +915551900001 is ready; the Android app will show its fake OTP."
echo "Use synthetic data only; do not enter PHI or repoint the stable production alias."
echo "Run end-disposable-test.sh with the same release SHA to erase test data."
