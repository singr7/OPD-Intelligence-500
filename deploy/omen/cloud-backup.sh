#!/usr/bin/env bash
set -euo pipefail

OPD_REPO="${OPD_REPO:-/home/opd/projects/opd}"
cd "$OPD_REPO"
set -a
# shellcheck disable=SC1091
source .env
set +a
: "${BACKUP_BUCKET:?set BACKUP_BUCKET}"
: "${AWS_REGION:?set AWS_REGION}"

BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 /data/backups
WORK_DIR="$(mktemp -d /data/backups/omen-"$BACKUP_ID".XXXXXX)"
cleanup() {
  rm -f "$WORK_DIR/database.dump"
  rmdir "$WORK_DIR" 2>/dev/null || true
}
trap cleanup EXIT
docker compose exec -T postgres pg_dump \
  -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
  --format=custom --compress=9 >"$WORK_DIR/database.dump"
CHECKSUM="$(sha256sum "$WORK_DIR/database.dump" | awk '{print $1}')"
SCHEMA_REVISION="$(docker compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" \
  -d "${POSTGRES_DB:-opd}" -tAc 'select version_num from alembic_version' | tr -d '[:space:]')"
SOURCE_SHA="$(git rev-parse HEAD)"
printf '{"backup_id":"%s","created_at":"%s","source_environment":"omen","source_commit":"%s","schema_revision":"%s","sha256":"%s","restore_result":"pending"}\n' \
  "$BACKUP_ID" "$(date -u +%FT%TZ)" "$SOURCE_SHA" "$SCHEMA_REVISION" "$CHECKSUM" \
  >"$WORK_DIR/manifest.json"
aws s3 cp "$WORK_DIR/database.dump" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/database.dump" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
aws s3 cp "$WORK_DIR/manifest.json" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/manifest.json" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
echo "$BACKUP_ID"
