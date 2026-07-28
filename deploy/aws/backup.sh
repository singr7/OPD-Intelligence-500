#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
load_env
: "${BACKUP_BUCKET:?set BACKUP_BUCKET}"
: "${AWS_REGION:?set AWS_REGION}"

BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$OPD_DATA/backups/$BACKUP_ID"
install -d -m 0700 "$WORK_DIR"

cleanup() {
  rm -f "$WORK_DIR/database.dump"
}
trap cleanup EXIT

compose exec -T postgres pg_dump \
  -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
  --format=custom --compress=9 >"$WORK_DIR/database.dump"
CHECKSUM="$(sha256sum "$WORK_DIR/database.dump" | awk '{print $1}')"
SCHEMA_REVISION="$(compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" -tAc 'select version_num from alembic_version' | tr -d '[:space:]')"
SOURCE_SHA="$(cat "$RELEASES_DIR/current-sha")"

printf '{"backup_id":"%s","created_at":"%s","source_commit":"%s","schema_revision":"%s","sha256":"%s","restore_result":"pending"}\n' \
  "$BACKUP_ID" "$(date -u +%FT%TZ)" "$SOURCE_SHA" "$SCHEMA_REVISION" "$CHECKSUM" \
  >"$WORK_DIR/manifest.json"

aws s3 cp "$WORK_DIR/database.dump" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/database.dump" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
aws s3 cp "$WORK_DIR/manifest.json" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/manifest.json" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
rm -f "$WORK_DIR/database.dump"
echo "$BACKUP_ID"
