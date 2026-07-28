#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
if [[ $# -ne 2 || ! "$1" =~ ^[A-Za-z0-9.-]+$ || "$2" != *@* ]]; then
  echo "usage: enable-tls.sh <aws-hostname> <cert-email>" >&2
  exit 2
fi
HOSTNAME="$1"
EMAIL="$2"
TOKEN="$(curl -fsS -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' \
  http://169.254.169.254/latest/api/token)"
PUBLIC_IP="$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/public-ipv4)"
RESOLVED="$(getent ahostsv4 "$HOSTNAME" | awk 'NR == 1 {print $1}')"
if [[ "$RESOLVED" != "$PUBLIC_IP" ]]; then
  echo "refusing TLS: $HOSTNAME resolves to $RESOLVED, expected $PUBLIC_IP" >&2
  exit 3
fi

certbot certonly --non-interactive --agree-tos --email "$EMAIL" \
  --webroot -w /var/www/letsencrypt -d "$HOSTNAME"

install -o root -g root -m 0644 \
  "$SCRIPT_DIR/nginx/opd-proxy.conf" /etc/nginx/opd-proxy.conf

# Bring up TLS without HSTS first. A bad certificate or proxy health must not
# pin a broken HTTPS origin into clients for a year.
sed "s/AWS_HOSTNAME/$HOSTNAME/g" "$SCRIPT_DIR/nginx/opd-tls.conf" \
  >/etc/nginx/sites-available/opd.conf.next
normalize_nginx_http2_syntax /etc/nginx/sites-available/opd.conf.next
sed -i '/Strict-Transport-Security/d' /etc/nginx/sites-available/opd.conf.next
mv /etc/nginx/sites-available/opd.conf.next /etc/nginx/sites-available/opd.conf
nginx -t
systemctl reload nginx
curl -fsS "https://$HOSTNAME/api/health" >/dev/null

sed "s/AWS_HOSTNAME/$HOSTNAME/g" "$SCRIPT_DIR/nginx/opd-tls.conf" \
  >/etc/nginx/sites-available/opd.conf.next
normalize_nginx_http2_syntax /etc/nginx/sites-available/opd.conf.next
mv /etc/nginx/sites-available/opd.conf.next /etc/nginx/sites-available/opd.conf
nginx -t
systemctl reload nginx
certbot renew --dry-run
systemctl enable --now certbot.timer
echo "TLS, HTTPS health, renewal dry-run, and HSTS activation passed"
