#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
load_env
compose stop api voice-gw worker beat web
set_database_read_only on
write_writer_env 0
compose up -d --wait api web
[[ "$(writer_setting)" == "on" ]]
echo "writes quiesced; API/web restarted on read-only database connections"
