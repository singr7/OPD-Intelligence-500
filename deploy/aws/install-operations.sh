#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
for unit in "$SCRIPT_DIR"/systemd/*.{service,timer}; do
  install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload
systemctl enable --now opd-backup.timer opd-restore-verify.timer opd-health-metrics.timer
systemctl list-timers --all 'opd-*'
