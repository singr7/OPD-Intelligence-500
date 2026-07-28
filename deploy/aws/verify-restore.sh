#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
load_env
: "${BACKUP_BUCKET:?set BACKUP_BUCKET}"
: "${AWS_REGION:?set AWS_REGION}"
# shellcheck disable=SC2016
BACKUP_ID="${1:-$(aws s3api list-objects-v2 --bucket "$BACKUP_BUCKET" --prefix database/ \
  --query 'reverse(sort_by(Contents[?ends_with(Key, `manifest.json`)], &LastModified))[0].Key' \
  --output text | cut -d/ -f2)}"
[[ "$BACKUP_ID" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]
VERIFY_DB="opd_restore_${BACKUP_ID//[^0-9]/}"
WORK_DIR="$OPD_DATA/backups/verify-$BACKUP_ID"
install -d -m 0700 "$WORK_DIR"
cleanup() {
  compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
    dropdb -U "${POSTGRES_USER:-opd}" --if-exists "$VERIFY_DB" >/dev/null 2>&1 || true
  rm -f "$WORK_DIR/database.dump"
}
trap cleanup EXIT

aws s3 cp "s3://$BACKUP_BUCKET/database/$BACKUP_ID/database.dump" "$WORK_DIR/database.dump" \
  --region "$AWS_REGION" --only-show-errors
aws s3 cp "s3://$BACKUP_BUCKET/database/$BACKUP_ID/manifest.json" "$WORK_DIR/manifest.json" \
  --region "$AWS_REGION" --only-show-errors
EXPECTED="$(sed -n 's/.*"sha256":"\([0-9a-f]\{64\}\)".*/\1/p' "$WORK_DIR/manifest.json")"
ACTUAL="$(sha256sum "$WORK_DIR/database.dump" | awk '{print $1}')"
[[ -n "$EXPECTED" && "$EXPECTED" == "$ACTUAL" ]]

compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
  createdb -U "${POSTGRES_USER:-opd}" "$VERIFY_DB"
compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
  pg_restore -U "${POSTGRES_USER:-opd}" -d "$VERIFY_DB" --no-owner --exit-on-error \
  <"$WORK_DIR/database.dump"
compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" -d "$VERIFY_DB" \
  -v ON_ERROR_STOP=1 -tAc "select version_num from alembic_version" >/dev/null
compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" -d "$VERIFY_DB" \
  -v ON_ERROR_STOP=1 -tAc "select count(*) from hospitals" >/dev/null

python3 - "$WORK_DIR/manifest.json" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
manifest = json.loads(path.read_text())
manifest["restore_result"] = "verified"
manifest["restore_verified_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
path.write_text(json.dumps(manifest, separators=(",", ":")) + "\n")
PY
aws s3 cp "$WORK_DIR/manifest.json" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/manifest.json" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
echo "isolated restore verified for $BACKUP_ID"
