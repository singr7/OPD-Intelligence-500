#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
if [[ $# -lt 3 || $# -gt 4 || ! "$1" =~ ^[A-Za-z0-9.-]+$ \
  || ! "$2" =~ ^/[A-Za-z0-9._/-]+$ || ! "$3" =~ ^/[A-Za-z0-9._/-]+$ \
  || ! "${4:-strict}" =~ ^(full|strict)$ ]]; then
  echo "usage: enable-cloudflare-tls.sh <hostname> <certificate-pem> <private-key> [full|strict]" >&2
  exit 2
fi
HOSTNAME="$1"
CERTIFICATE="$2"
PRIVATE_KEY="$3"
CLOUDFLARE_MODE="${4:-strict}"

if [[ ! -f "$CERTIFICATE" || ! -f "$PRIVATE_KEY" ]]; then
  echo "certificate or private key does not exist" >&2
  exit 3
fi

openssl x509 -in "$CERTIFICATE" -noout -checkend 0 >/dev/null
if [[ "$CLOUDFLARE_MODE" == "strict" ]]; then
  openssl x509 -in "$CERTIFICATE" -noout -checkhost "$HOSTNAME" >/dev/null
else
  if ! openssl x509 -in "$CERTIFICATE" -noout -checkhost "$HOSTNAME" >/dev/null 2>&1; then
    echo "WARNING: using Cloudflare Full mode with a certificate that does not match $HOSTNAME" >&2
    echo "The origin connection is encrypted but Cloudflare will not authenticate its hostname." >&2
  fi
fi
openssl pkey -in "$PRIVATE_KEY" -noout >/dev/null

CERT_KEY_SHA="$(
  openssl x509 -in "$CERTIFICATE" -pubkey -noout |
    openssl pkey -pubin -outform DER 2>/dev/null |
    sha256sum | awk '{print $1}'
)"
PRIVATE_KEY_SHA="$(
  openssl pkey -in "$PRIVATE_KEY" -pubout -outform DER 2>/dev/null |
    sha256sum | awk '{print $1}'
)"
if [[ "$CERT_KEY_SHA" != "$PRIVATE_KEY_SHA" ]]; then
  echo "certificate and private key do not match" >&2
  exit 4
fi

chown root:root "$CERTIFICATE" "$PRIVATE_KEY"
chmod 0644 "$CERTIFICATE"
chmod 0600 "$PRIVATE_KEY"

install -o root -g root -m 0644 \
  "$SCRIPT_DIR/nginx/opd-proxy.conf" /etc/nginx/opd-proxy.conf
"$SCRIPT_DIR/configure-cloudflare-real-ip.sh"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
sed \
  -e "s|AWS_HOSTNAME|$HOSTNAME|g" \
  -e "s|TLS_CERTIFICATE_KEY|$PRIVATE_KEY|g" \
  -e "s|TLS_CERTIFICATE|$CERTIFICATE|g" \
  "$SCRIPT_DIR/nginx/opd-cloudflare-tls.conf" >"$TMP_DIR/opd.conf"
normalize_nginx_http2_syntax "$TMP_DIR/opd.conf"

# Start HTTPS without HSTS. This local request deliberately bypasses public DNS
# and certificate trust; certificate lifetime and key matching were checked above.
grep -v Strict-Transport-Security "$TMP_DIR/opd.conf" \
  >/etc/nginx/sites-available/opd.conf.next
mv /etc/nginx/sites-available/opd.conf.next /etc/nginx/sites-available/opd.conf
nginx -t
systemctl reload nginx
curl -kfsS --max-time 30 \
  --resolve "$HOSTNAME:443:127.0.0.1" \
  "https://$HOSTNAME/api/health" >/dev/null

if ! curl -fsS --max-time 30 "https://$HOSTNAME/api/health" >/dev/null; then
  echo "origin HTTPS passed, but public HTTPS failed." >&2
  echo "Set Cloudflare SSL/TLS mode to $CLOUDFLARE_MODE, confirm the orange-cloud DNS record," >&2
  echo "and rerun this command. HSTS has not been enabled." >&2
  exit 5
fi

install -o root -g root -m 0644 "$TMP_DIR/opd.conf" \
  /etc/nginx/sites-available/opd.conf
nginx -t
systemctl reload nginx
curl -fsS --max-time 30 "https://$HOSTNAME/api/health" >/dev/null

openssl x509 -in "$CERTIFICATE" -noout -subject -issuer -dates
echo "Cloudflare $CLOUDFLARE_MODE public HTTPS passed; HSTS is active"
echo "certificate renewal is external to Certbot; monitor and replace it before expiry"
