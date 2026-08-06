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

# ---------------------------------------------------------------------------
# Does the backup actually hold the pages the restored rows point at?
#
# This is the check that matters, and it is the one that was missing: until now
# a drill could report "verified" on a backup containing no scanned reports at
# all, because it only ever asked whether the *database* restored. A restore
# that brings back an extraction whose photographs are gone is not a restore of
# a medical record — the page route answers 410 and the doctor is told, but the
# biopsy report is still gone.
#
# Bounded to the newest keys rather than all of them: the drill runs daily and
# the store grows without limit, and a newest-first sample is where an ordering
# bug in backup.sh shows up first — the page scanned during the backup is
# exactly the one a wrong order drops.
# ---------------------------------------------------------------------------
RECORDS_PREFIX="${RECORDS_PREFIX:-pages}"
SAMPLE="${RECORDS_VERIFY_SAMPLE:-50}"
KEYS_FILE="$WORK_DIR/page-keys.txt"
compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" -d "$VERIFY_DB" \
  -v ON_ERROR_STOP=1 -tAc "
    select jsonb_array_elements_text(d.object_keys)
    from (
      select object_keys, created_at
      from medical_documents
      where deleted_at is null and jsonb_array_length(object_keys) > 0
      order by created_at desc
      limit 100
    ) d
    limit $SAMPLE" | tr -d '\r' | sed '/^$/d' >"$KEYS_FILE"

CHECKED=0
MISSING=0
while IFS= read -r key; do
  [[ -n "$key" ]] || continue
  CHECKED=$((CHECKED + 1))
  if ! aws s3api head-object --bucket "$BACKUP_BUCKET" --key "$RECORDS_PREFIX/$key" \
    --region "$AWS_REGION" >/dev/null 2>&1; then
    MISSING=$((MISSING + 1))
    echo "MISSING PAGE: $RECORDS_PREFIX/$key" >&2
  fi
done <"$KEYS_FILE"
rm -f "$KEYS_FILE"

if [[ "$MISSING" -gt 0 ]]; then
  echo "$MISSING of $CHECKED sampled pages are not in the backup — this backup is NOT restorable" >&2
  exit 4
fi
echo "pages checked: $CHECKED (all present)"

python3 - "$WORK_DIR/manifest.json" "$CHECKED" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
manifest = json.loads(path.read_text())
manifest["restore_result"] = "verified"
manifest["restore_verified_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
# How many page objects were confirmed present, so "verified" on a backup with
# scanned documents in it cannot be confused with "verified" on one without.
manifest["pages_checked"] = int(sys.argv[2])
path.write_text(json.dumps(manifest, separators=(",", ":")) + "\n")
PY
aws s3 cp "$WORK_DIR/manifest.json" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/manifest.json" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
echo "isolated restore verified for $BACKUP_ID"
