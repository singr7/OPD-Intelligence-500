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

# ---------------------------------------------------------------------------
# The database first, the pages second. **This order is the whole correctness
# argument** and must not be swapped for tidiness.
#
# Pages are append-only, so every page the dump references already existed on
# disk when the dump was taken, and a sync that runs afterwards is guaranteed to
# carry it. The reverse order loses exactly the newest report — the one a
# coordinator scanned during the backup — which is both the most likely to
# matter and the least likely to be noticed.
#
# Pages uploaded between the dump and the sync are harmless: they are orphans
# referenced by no restored row, and the next dump adopts them.
# ---------------------------------------------------------------------------
compose exec -T postgres pg_dump \
  -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
  --format=custom --compress=9 >"$WORK_DIR/database.dump"
CHECKSUM="$(sha256sum "$WORK_DIR/database.dump" | awk '{print $1}')"

RECORDS_DIR="$(records_dir)"
RECORDS_OBJECTS=0
RECORDS_BYTES=0
if [[ -d "$RECORDS_DIR" ]]; then
  aws s3 sync "$RECORDS_DIR/" "s3://$BACKUP_BUCKET/$RECORDS_PREFIX/" \
    --region "$AWS_REGION" --sse AES256 --only-show-errors
  RECORDS_OBJECTS="$(find "$RECORDS_DIR" -type f ! -name '.partial-*' | wc -l | tr -d ' ')"
  RECORDS_BYTES="$(du -sb "$RECORDS_DIR" | awk '{print $1}')"
else
  # Not an error — a box where nothing has been scanned yet has no directory.
  # Recorded as zero so a manifest can never be mistaken for one that ran before
  # pages were part of the backup at all.
  echo "no records directory at $RECORDS_DIR; recording zero pages" >&2
fi
SCHEMA_REVISION="$(compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" -tAc 'select version_num from alembic_version' | tr -d '[:space:]')"
SOURCE_SHA="$(cat "$RELEASES_DIR/current-sha")"

printf '{"backup_id":"%s","created_at":"%s","source_commit":"%s","schema_revision":"%s","sha256":"%s","records_prefix":"%s","records_objects":%s,"records_bytes":%s,"restore_result":"pending"}\n' \
  "$BACKUP_ID" "$(date -u +%FT%TZ)" "$SOURCE_SHA" "$SCHEMA_REVISION" "$CHECKSUM" \
  "$RECORDS_PREFIX" "$RECORDS_OBJECTS" "$RECORDS_BYTES" \
  >"$WORK_DIR/manifest.json"

aws s3 cp "$WORK_DIR/database.dump" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/database.dump" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
aws s3 cp "$WORK_DIR/manifest.json" "s3://$BACKUP_BUCKET/database/$BACKUP_ID/manifest.json" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
rm -f "$WORK_DIR/database.dump"
echo "$BACKUP_ID"
