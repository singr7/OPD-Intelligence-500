#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
if [[ $# -ne 1 || ! "$1" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "usage: bootstrap.sh <aws-hostname>" >&2
  exit 2
fi
AWS_HOSTNAME="$1"

install -d -m 0750 -o root -g docker "$OPD_RUNTIME" "$RELEASES_DIR"
install -d -m 0750 -o root -g docker \
  "$OPD_DATA/postgres" "$OPD_DATA/redis" "$OPD_DATA/releases" "$OPD_DATA/backups"
install -d -m 0755 /var/www/letsencrypt

if [[ ! -e "$WRITER_ENV" ]]; then
  write_writer_env 0
fi

sed "s/AWS_HOSTNAME/$AWS_HOSTNAME/g" "$SCRIPT_DIR/nginx/opd-http.conf" \
  >/etc/nginx/sites-available/opd.conf
install -m 0644 "$SCRIPT_DIR/nginx/opd-proxy.conf" /etc/nginx/opd-proxy.conf
ln -sfn /etc/nginx/sites-available/opd.conf /etc/nginx/sites-enabled/opd.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "bootstrap complete; add application.env with mode 0600, then deploy an immutable SHA"
