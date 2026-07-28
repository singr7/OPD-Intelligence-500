#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
require_sha "${1:-}"
if [[ "${2:-}" != "--confirm-delete-test-data" ]]; then
  echo "usage: end-disposable-test.sh <full-release-sha> --confirm-delete-test-data" >&2
  exit 2
fi
REQUESTED_SHA="$1"
load_env
IMAGE_TAG="$REQUESTED_SHA"
export IMAGE_TAG

MARKER="$RELEASES_DIR/disposable-test-active"
if [[ ! -s "$MARKER" ]] || ! grep -qx 'mode=disposable-no-phi' "$MARKER"; then
  echo "refusing destructive cleanup: disposable test marker is absent" >&2
  exit 3
fi
if [[ -s "$RELEASES_DIR/restored-backup" ]]; then
  echo "refusing destructive cleanup: verified restore marker exists" >&2
  exit 3
fi

prepare_release_images
compose stop api voice-gw worker beat web
set_database_read_only off
compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
  dropdb -U "${POSTGRES_USER:-opd}" --if-exists "${POSTGRES_DB:-opd}"
compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
  createdb -U "${POSTGRES_USER:-opd}" "${POSTGRES_DB:-opd}"
compose exec -T redis redis-cli FLUSHALL >/dev/null
compose --profile migration run --rm migrate
set_database_read_only on
write_writer_env 0
compose up -d --wait postgres redis
compose up -d --wait --force-recreate api web
rm -f "$MARKER"

[[ "$(writer_setting)" == "on" ]]
curl -fsS http://127.0.0.1:18080/health >/dev/null
echo "disposable AWS test data erased; AWS is read-only standby again"
