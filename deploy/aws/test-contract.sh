#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEST_RUNTIME="$(mktemp -d)"
trap 'rm -rf "$TEST_RUNTIME"' EXIT

printf 'POSTGRES_PASSWORD=test-only\n' >"$TEST_RUNTIME/application.env"
printf 'OPD_WRITER_ENABLED=0\n' >"$TEST_RUNTIME/writer.env"

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

for script in "$SCRIPT_DIR"/*.sh "$REPO_ROOT/infra/user_data.sh"; do
  bash -n "$script"
done

grep -q 'Strict-Transport-Security' "$SCRIPT_DIR/nginx/opd-tls.conf"
if grep -q 'Strict-Transport-Security' "$SCRIPT_DIR/nginx/opd-http.conf"; then
  echo "HSTS must not be enabled before TLS verification" >&2
  exit 1
fi
echo "AWS deployment contract: ok"
