#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
require_sha "${1:-}"
REQUESTED_SHA="$1"
load_env
# `load_env` sources release.env, which carries the *last deployed* IMAGE_TAG.
# Assigning IMAGE_TAG before that call lets the old value silently overwrite the
# SHA the operator asked for, so hold the request in its own variable and restore
# it afterwards (activate-disposable-test.sh already did this; these two did not).
IMAGE_TAG="$REQUESTED_SHA"
export IMAGE_TAG
write_release_env "$IMAGE_TAG"

if [[ "$IMAGE_TAG" == "latest" ]]; then
  echo "mutable latest tags are forbidden" >&2
  exit 2
fi
prepare_release_images
compose up -d --wait postgres redis
compose stop api voice-gw worker beat web
set_database_read_only off
compose --profile migration run --rm migrate
if [[ "${OPD_WRITER_ENABLED:-0}" == "1" ]]; then
  set_database_read_only off
else
  set_database_read_only on
fi
compose up -d --wait postgres redis api voice-gw worker beat web

curl -fsS http://127.0.0.1:18080/health >/dev/null
curl -fsS http://127.0.0.1:13000/api/health >/dev/null

install -d -m 0750 "$RELEASES_DIR"
umask 027
printf '%s\n' "$IMAGE_TAG" >"$RELEASES_DIR/current-sha"
echo "deployed $IMAGE_TAG; writer=$(writer_setting)"
