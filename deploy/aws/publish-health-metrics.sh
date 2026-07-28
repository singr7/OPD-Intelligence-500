#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
load_env
: "${AWS_REGION:?set AWS_REGION}"
: "${BACKUP_BUCKET:?set BACKUP_BUCKET}"
: "${PUBLIC_HOSTNAME:?set PUBLIC_HOSTNAME}"
ENVIRONMENT="${ENVIRONMENT:-pilot}"

if curl -fsS --max-time 10 "https://$PUBLIC_HOSTNAME/api/health" >/dev/null; then
  PUBLIC_HEALTH=1
else
  PUBLIC_HEALTH=0
fi
# shellcheck disable=SC2016
LAST_BACKUP="$(aws s3api list-objects-v2 --bucket "$BACKUP_BUCKET" --prefix database/ \
  --query 'max_by(Contents[?ends_with(Key, `manifest.json`)], &LastModified).LastModified' \
  --output text)"
if [[ "$LAST_BACKUP" == "None" || -z "$LAST_BACKUP" ]]; then
  BACKUP_AGE=999999
else
  BACKUP_EPOCH="$(date -d "$LAST_BACKUP" +%s)"
  BACKUP_AGE="$(( $(date -u +%s) - BACKUP_EPOCH ))"
fi

aws cloudwatch put-metric-data --region "$AWS_REGION" --namespace OPD/Standby \
  --metric-data \
  "MetricName=PublicHealth,Dimensions=[{Name=Environment,Value=$ENVIRONMENT},{Name=Host,Value=$PUBLIC_HOSTNAME}],Value=$PUBLIC_HEALTH,Unit=Count" \
  "MetricName=BackupAgeSeconds,Dimensions=[{Name=Environment,Value=$ENVIRONMENT}],Value=$BACKUP_AGE,Unit=Seconds"
