#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
require_sha "${1:-}"
IMAGE_TAG="$1"
load_env
export IMAGE_TAG

prepare_release_images
write_release_env "$IMAGE_TAG"
compose up -d --wait api voice-gw worker beat web
curl -fsS http://127.0.0.1:18080/health >/dev/null
curl -fsS http://127.0.0.1:18080/environment | grep -q "$IMAGE_TAG"
printf '%s\n' "$IMAGE_TAG" >"$RELEASES_DIR/current-sha"
echo "application rolled back to $IMAGE_TAG; data volume and writer state unchanged"
