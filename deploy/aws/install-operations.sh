#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
for unit in "$SCRIPT_DIR"/systemd/*.{service,timer}; do
  install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
install -d -m 0755 /var/log/opd
systemctl daemon-reload
systemctl enable --now opd-backup.timer opd-restore-verify.timer opd-health-metrics.timer

# Enabled, not started. `--now` would run a `compose up --wait` against a stack
# an operator may be in the middle of working on; the point of this unit is the
# *next* boot. Test it deliberately with `systemctl start opd-boot`.
systemctl enable opd-boot.service

systemctl list-timers --all 'opd-*'
systemctl is-enabled opd-boot.service
