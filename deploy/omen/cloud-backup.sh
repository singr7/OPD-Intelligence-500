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
# Database first, pages second — see the long note in deploy/aws/backup.sh. The
# order is the correctness argument: pages are append-only, so a sync taken
# after the dump necessarily contains every page the dump references, and the
# reverse loses precisely the report scanned during the backup.
docker compose exec -T postgres pg_dump \
  -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
  --format=custom --compress=9 >"$WORK_DIR/database.dump"
CHECKSUM="$(sha256sum "$WORK_DIR/database.dump" | awk '{print $1}')"

# Omen runs the repo-root compose file, where /data/records is a *named volume*
# rather than a host bind — so its real path is asked of Docker instead of
# assumed. Hardcoding `<project>_recordsdata` would break the day someone
# renames the checkout directory.
RECORDS_PREFIX="pages"
RECORDS_DIR="$(docker inspect \
  -f '{{range .Mounts}}{{if eq .Destination "/data/records"}}{{.Source}}{{end}}{{end}}' \
  "$(docker compose ps -q api | head -n1)" 2>/dev/null || true)"
RECORDS_OBJECTS=0
RECORDS_BYTES=0
if [[ -n "$RECORDS_DIR" && -d "$RECORDS_DIR" ]]; then
  aws s3 sync "$RECORDS_DIR/" "s3://$BACKUP_BUCKET/$RECORDS_PREFIX/" \
    --region "$AWS_REGION" --sse AES256 --only-show-errors
  RECORDS_OBJECTS="$(find "$RECORDS_DIR" -type f ! -name '.partial-*' | wc -l | tr -d ' ')"
  RECORDS_BYTES="$(du -sb "$RECORDS_DIR" | awk '{print $1}')"
else
  echo "no records volume found for the api container; recording zero pages" >&2
fi
SCHEMA_REVISION="$(docker compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" \
  -d "${POSTGRES_DB:-opd}" -tAc 'select version_num from alembic_version' | tr -d '[:space:]')"
SOURCE_SHA="$(git rev-parse HEAD)"
printf '{"backup_id":"%s","created_at":"%s","source_environment":"omen","source_commit":"%s","schema_revision":"%s","sha256":"%s","records_prefix":"%s","records_objects":%s,"records_bytes":%s,"restore_result":"pending"}\n' \
  "$BACKUP_ID" "$(date -u +%FT%TZ)" "$SOURCE_SHA" "$SCHEMA_REVISION" "$CHECKSUM" \
  "$RECORDS_PREFIX" "$RECORDS_OBJECTS" "$RECORDS_BYTES" \
  >"$WORK_DIR/manifest.json"
aws s3 cp "$WORK_DIR/database.dump" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/database.dump" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
aws s3 cp "$WORK_DIR/manifest.json" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/manifest.json" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
echo "$BACKUP_ID"
