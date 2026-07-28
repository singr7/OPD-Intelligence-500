#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
: "${AWS_REGION:?set AWS_REGION outside the secret for the initial fetch}"
: "${RUNTIME_SECRET_ARN:?set RUNTIME_SECRET_ARN}"
install -d -m 0700 "$OPD_RUNTIME"
SECRET_TMP="$(mktemp "$OPD_RUNTIME/secret.XXXXXX")"
ENV_TMP="$(mktemp "$OPD_RUNTIME/application.env.XXXXXX")"
cleanup() {
  shred -u "$SECRET_TMP" 2>/dev/null || rm -f "$SECRET_TMP"
  shred -u "$ENV_TMP" 2>/dev/null || rm -f "$ENV_TMP"
}
trap cleanup EXIT
chmod 0600 "$SECRET_TMP" "$ENV_TMP"

aws secretsmanager get-secret-value \
  --region "$AWS_REGION" \
  --secret-id "$RUNTIME_SECRET_ARN" \
  --query SecretString \
  --output text >"$SECRET_TMP"
python3 "$SCRIPT_DIR/secret-to-env.py" "$SECRET_TMP" "$ENV_TMP"
chown root:root "$ENV_TMP"
chmod 0600 "$ENV_TMP"
mv "$ENV_TMP" "$APPLICATION_ENV"
shred -u "$SECRET_TMP" 2>/dev/null || rm -f "$SECRET_TMP"
trap - EXIT
echo "runtime environment replaced from Secrets Manager; values were not logged"
