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

if [[ "$IMAGE_TAG" == "latest" ]]; then
  echo "mutable latest tags are forbidden" >&2
  exit 2
fi
: "${AWS_REGION:?set AWS_REGION}"
: "${ECR_REGISTRY:?set ECR_REGISTRY}"

aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "$ECR_REGISTRY"

compose pull api voice-gw worker beat web
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
