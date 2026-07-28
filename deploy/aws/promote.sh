#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
load_env
[[ -s "$RELEASES_DIR/restored-backup" ]] || {
  echo "refusing promotion: no verified restore marker" >&2
  exit 3
}
[[ "$(writer_setting)" == "on" ]] || {
  echo "refusing promotion: database is already writable" >&2
  exit 3
}

compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
  -v ON_ERROR_STOP=1 -c "select count(*) from alembic_version" -c "select count(*) from hospitals"
set_database_read_only off
write_writer_env 1
compose up -d --wait api voice-gw worker beat web
curl -fsS http://127.0.0.1:18080/health >/dev/null
echo "promotion complete; switch stable DNS/pairing only after the public health gate"
