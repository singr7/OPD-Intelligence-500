#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
if [[ $# -lt 2 || $# -gt 3 || ! "$1" =~ ^[A-Za-z0-9.-]+$ || ! "$2" =~ ^[a-z0-9-]+$ ]]; then
  echo "usage: create-local-runtime-env.sh <public-hostname> <aws-region> [backup-bucket]" >&2
  exit 2
fi
PUBLIC_HOSTNAME="$1"
AWS_REGION="$2"
BACKUP_BUCKET="${3:-}"

if [[ -e "$APPLICATION_ENV" ]]; then
  echo "refusing to replace existing $APPLICATION_ENV" >&2
  exit 3
fi

install -d -m 0700 "$OPD_RUNTIME"
POSTGRES_PASSWORD="$(openssl rand -hex 32)"
JWT_SECRET="$(openssl rand -hex 32)"
SECRETS_KEY="$(python3 -c \
  'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"

umask 077
{
  printf 'POSTGRES_USER=opd\n'
  printf 'POSTGRES_PASSWORD=%s\n' "$POSTGRES_PASSWORD"
  printf 'POSTGRES_DB=opd\n'
  printf 'JWT_SECRET=%s\n' "$JWT_SECRET"
  printf 'SECRETS_KEY=%s\n' "$SECRETS_KEY"
  printf 'AWS_REGION=%s\n' "$AWS_REGION"
  printf 'ECR_REGISTRY=opd-local\n'
  printf 'OPD_IMAGE_SOURCE=local\n'
  printf 'BACKUP_BUCKET=%s\n' "$BACKUP_BUCKET"
  printf 'PUBLIC_HOSTNAME=%s\n' "$PUBLIC_HOSTNAME"
  printf 'ENVIRONMENT_ID=aws\n'
  printf 'ENVIRONMENT_NAME="OPD Cloud standby"\n'
} >"$APPLICATION_ENV"
chown root:root "$APPLICATION_ENV"
chmod 0600 "$APPLICATION_ENV"

echo "created $APPLICATION_ENV as root:root 0600 with generated database/JWT/Fernet secrets"
echo "provider credentials were not added; configure and test them through the Channels console"

