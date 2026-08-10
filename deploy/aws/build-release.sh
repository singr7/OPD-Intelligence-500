#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

SHA="${1:-$(git rev-parse HEAD)}"
if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: build-release.sh <full-commit-sha>" >&2
  exit 2
fi
if [[ "$(git rev-parse HEAD)" != "$SHA" ]]; then
  echo "refusing: requested SHA is not the checked-out commit" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "refusing: release images must be built from a clean worktree" >&2
  exit 3
fi

: "${AWS_REGION:?set AWS_REGION}"
: "${ECR_REGISTRY:?set ECR_REGISTRY}"
: "${NEXT_PUBLIC_API_BASE:?set the public HTTPS /api base for the web build}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
MANIFEST_DIR="${MANIFEST_DIR:-$REPO_ROOT/releases/aws}"

aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "$ECR_REGISTRY"
docker buildx inspect opd-release-builder >/dev/null 2>&1 ||
  docker buildx create --name opd-release-builder --use
docker buildx use opd-release-builder
docker buildx inspect --bootstrap >/dev/null

docker buildx build --platform "$PLATFORMS" --provenance=true --sbom=true \
  -t "$ECR_REGISTRY/opd-api:$SHA" --push backend
docker buildx build --platform "$PLATFORMS" --provenance=true --sbom=true \
  -t "$ECR_REGISTRY/opd-worker:$SHA" --push backend
docker buildx build --platform "$PLATFORMS" --provenance=true --sbom=true \
  -f voice-gw/Dockerfile -t "$ECR_REGISTRY/opd-voice-gw:$SHA" --push .
docker buildx build --platform "$PLATFORMS" --provenance=true --sbom=true \
  --build-arg "NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE" \
  --build-arg NEXT_PUBLIC_KIOSK_SERVER_STT=1 \
  --build-arg NEXT_PUBLIC_KIOSK_SERVER_TTS=1 \
  --build-arg NEXT_PUBLIC_KIOSK_ADAPTIVE=1 \
  --build-arg "NEXT_PUBLIC_PASS_GEOMETRY=${PASS_GEOMETRY:-roll80}" \
  --build-arg "NEXT_PUBLIC_PASS_AUTOPRINT=${PASS_AUTOPRINT:-0}" \
  --build-arg "NEXT_PUBLIC_PRINT_BRIDGE_URL=${PRINT_BRIDGE_URL:-}" \
  -t "$ECR_REGISTRY/opd-web:$SHA" --push web

digest_of() {
  aws ecr describe-images \
    --region "$AWS_REGION" \
    --repository-name "opd-$1" \
    --image-ids "imageTag=$SHA" \
    --query 'imageDetails[0].imageDigest' \
    --output text
}

API_DIGEST="$(digest_of api)"
VOICE_DIGEST="$(digest_of voice-gw)"
WORKER_DIGEST="$(digest_of worker)"
WEB_DIGEST="$(digest_of web)"
for digest in "$API_DIGEST" "$VOICE_DIGEST" "$WORKER_DIGEST" "$WEB_DIGEST"; do
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
done

install -d -m 0750 "$MANIFEST_DIR"
if [[ -f "$MANIFEST_DIR/current.json" ]]; then
  cp "$MANIFEST_DIR/current.json" "$MANIFEST_DIR/previous.json"
fi
umask 027
{
  printf '{\n  "schema": 1,\n  "commit": "%s",\n  "created_at": "%s",\n  "platforms": "%s",\n  "images": {\n' \
    "$SHA" "$(date -u +%FT%TZ)" "$PLATFORMS"
  printf '    "api": "%s/opd-api@%s",\n' "$ECR_REGISTRY" "$API_DIGEST"
  printf '    "voice-gw": "%s/opd-voice-gw@%s",\n' "$ECR_REGISTRY" "$VOICE_DIGEST"
  printf '    "worker": "%s/opd-worker@%s",\n' "$ECR_REGISTRY" "$WORKER_DIGEST"
  printf '    "web": "%s/opd-web@%s"\n  }\n}\n' "$ECR_REGISTRY" "$WEB_DIGEST"
} >"$MANIFEST_DIR/$SHA.json"
cp "$MANIFEST_DIR/$SHA.json" "$MANIFEST_DIR/current.json"

if [[ -n "${RELEASE_MANIFEST_BUCKET:-}" ]]; then
  aws s3 cp "$MANIFEST_DIR/$SHA.json" \
    "s3://$RELEASE_MANIFEST_BUCKET/releases/$SHA.json" \
    --region "$AWS_REGION" --sse AES256 --only-show-errors
fi
echo "$MANIFEST_DIR/$SHA.json"
