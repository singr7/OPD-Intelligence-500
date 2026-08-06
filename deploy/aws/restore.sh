#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
BACKUP_ID="${1:-}"
CONFIRM="${2:-}"
if [[ ! "$BACKUP_ID" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "usage: restore.sh <YYYYMMDDTHHMMSSZ> [--confirm-live-writer]" >&2
  exit 2
fi
load_env
: "${BACKUP_BUCKET:?set BACKUP_BUCKET}"
: "${AWS_REGION:?set AWS_REGION}"

if [[ "$(writer_setting)" != "on" && "$CONFIRM" != "--confirm-live-writer" ]]; then
  echo "refusing to restore over a live writer; quiesce it or pass --confirm-live-writer" >&2
  exit 3
fi

WORK_DIR="$OPD_DATA/backups/restore-$BACKUP_ID"
install -d -m 0700 "$WORK_DIR"
trap 'rm -f "$WORK_DIR/database.dump"' EXIT
aws s3 cp "s3://$BACKUP_BUCKET/database/$BACKUP_ID/database.dump" "$WORK_DIR/database.dump" \
  --region "$AWS_REGION" --only-show-errors
aws s3 cp "s3://$BACKUP_BUCKET/database/$BACKUP_ID/manifest.json" "$WORK_DIR/manifest.json" \
  --region "$AWS_REGION" --only-show-errors

EXPECTED="$(sed -n 's/.*"sha256":"\([0-9a-f]\{64\}\)".*/\1/p' "$WORK_DIR/manifest.json")"
ACTUAL="$(sha256sum "$WORK_DIR/database.dump" | awk '{print $1}')"
[[ -n "$EXPECTED" && "$EXPECTED" == "$ACTUAL" ]]

compose stop api voice-gw worker beat web
set_database_read_only on
compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
  dropdb -U "${POSTGRES_USER:-opd}" --if-exists "${POSTGRES_DB:-opd}"
compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
  createdb -U "${POSTGRES_USER:-opd}" "${POSTGRES_DB:-opd}"
compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
  pg_restore -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
  --clean --if-exists --no-owner --exit-on-error <"$WORK_DIR/database.dump"
# The pages, without which the restore is not a restore (doc 22 §2). Rows in
# `medical_documents` that come back pointing at bytes which are gone answer 410
# per page — visible, but a patient's biopsy report is still gone.
#
# Pulled *after* the database on purpose, mirroring the backup's ordering: the
# sync brings back everything the restored rows can reference, plus any orphan
# uploaded later, which nothing reads.
#
# No `--delete`: this adds the backed-up pages to whatever the box already
# holds. A restore must never be the thing that removes a scanned report.
RECORDS_DIR="$(records_dir)"
install -d -m 0700 "$RECORDS_DIR"
aws s3 sync "s3://$BACKUP_BUCKET/$RECORDS_PREFIX/" "$RECORDS_DIR/" \
  --region "$AWS_REGION" --only-show-errors
# The store writes 0600/0700 and the api runs as its own user inside the
# container; the sync creates files with the caller's umask, so put them back.
find "$RECORDS_DIR" -type d -exec chmod 0700 {} +
find "$RECORDS_DIR" -type f -exec chmod 0600 {} +

write_writer_env 0
echo "$BACKUP_ID" >"$RELEASES_DIR/restored-backup"
echo "restored $BACKUP_ID and synced pages into $RECORDS_DIR; application remains stopped"
