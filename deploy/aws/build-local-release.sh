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

if [[ "${OPD_IMAGE_SOURCE:-}" != "local" ]]; then
  echo "refusing local build: OPD_IMAGE_SOURCE must be local" >&2
  exit 2
fi
if [[ "${ECR_REGISTRY:-}" != "opd-local" ]]; then
  echo "refusing local build: ECR_REGISTRY must be the local image namespace opd-local" >&2
  exit 2
fi
if [[ "$(git -c safe.directory="$OPD_ROOT" -C "$OPD_ROOT" rev-parse HEAD)" != "$IMAGE_TAG" ]]; then
  echo "refusing: requested SHA is not the checked-out commit" >&2
  exit 2
fi
if [[ -n "$(git -c safe.directory="$OPD_ROOT" -C "$OPD_ROOT" \
  status --porcelain --untracked-files=normal)" ]]; then
  echo "refusing: release images must be built from a clean worktree" >&2
  exit 3
fi
: "${PUBLIC_HOSTNAME:?set PUBLIC_HOSTNAME}"

cd "$OPD_ROOT"
COMMON_LABEL="org.opencontainers.image.revision=$IMAGE_TAG"
docker build --pull --label "$COMMON_LABEL" \
  -t "$ECR_REGISTRY/opd-api:$IMAGE_TAG" \
  -t "$ECR_REGISTRY/opd-worker:$IMAGE_TAG" backend
docker build --pull --label "$COMMON_LABEL" -f voice-gw/Dockerfile \
  -t "$ECR_REGISTRY/opd-voice-gw:$IMAGE_TAG" .
# Server voice is the default and the right posture: the clip is transcribed and
# the read-aloud synthesised on the box, so patient audio never reaches a browser
# vendor. It requires an STT/TTS provider to actually be configured — this stack
# ships no Whisper or local TTS container, so an unconfigured host falls to the
# `fake` providers, which return a canned transcript and silence. That looks
# exactly like a broken microphone.
#
# On a disposable synthetic-data box with no provider configured, set these to 0
# to fall back to the browser's own Web Speech and get a working demo:
#
#   KIOSK_SERVER_STT=0 KIOSK_SERVER_TTS=0 sudo -E build-local-release.sh <sha>
#
# Chrome's recogniser ships the audio to Google. That is acceptable for synthetic
# data and is NOT acceptable once real patients speak into this. Never ship 0 to a
# host that could receive PHI.
docker build --pull --label "$COMMON_LABEL" \
  --build-arg "NEXT_PUBLIC_API_BASE=https://$PUBLIC_HOSTNAME/api" \
  --build-arg "NEXT_PUBLIC_KIOSK_SERVER_STT=${KIOSK_SERVER_STT:-1}" \
  --build-arg "NEXT_PUBLIC_KIOSK_SERVER_TTS=${KIOSK_SERVER_TTS:-1}" \
  --build-arg "NEXT_PUBLIC_KIOSK_ADAPTIVE=${KIOSK_ADAPTIVE:-1}" \
  -t "$ECR_REGISTRY/opd-web:$IMAGE_TAG" web

declare -A IDS
for image in api voice-gw worker web; do
  IDS["$image"]="$(docker image inspect \
    --format '{{.Id}}' "$ECR_REGISTRY/opd-$image:$IMAGE_TAG")"
  [[ "${IDS[$image]}" =~ ^sha256:[0-9a-f]{64}$ ]]
done

install -d -m 0750 "$RELEASES_DIR"
MANIFEST="$RELEASES_DIR/$IMAGE_TAG.local-images.json"
umask 027
{
  printf '{\n  "schema": 1,\n  "source": "local-build",\n'
  printf '  "commit": "%s",\n  "created_at": "%s",\n  "architecture": "%s",\n' \
    "$IMAGE_TAG" "$(date -u +%FT%TZ)" "$(uname -m)"
  printf '  "images": {\n'
  printf '    "api": "%s",\n' "${IDS[api]}"
  printf '    "voice-gw": "%s",\n' "${IDS[voice-gw]}"
  printf '    "worker": "%s",\n' "${IDS[worker]}"
  printf '    "web": "%s"\n  }\n}\n' "${IDS[web]}"
} >"$MANIFEST"
chown root:root "$MANIFEST"
chmod 0640 "$MANIFEST"

echo "built local full-SHA images; manifest: $MANIFEST"
