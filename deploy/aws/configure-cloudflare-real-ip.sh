#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

curl -fsS --max-time 30 https://www.cloudflare.com/ips-v4 \
  >"$TMP_DIR/ips-v4"
curl -fsS --max-time 30 https://www.cloudflare.com/ips-v6 \
  >"$TMP_DIR/ips-v6"

python3 - "$TMP_DIR/ips-v4" "$TMP_DIR/ips-v6" <<'PY'
import ipaddress
import pathlib
import sys

for filename in sys.argv[1:]:
    lines = [line.strip() for line in pathlib.Path(filename).read_text().splitlines()]
    if not lines or any(not line for line in lines):
        raise SystemExit(f"empty Cloudflare IP range response: {filename}")
    for line in lines:
        ipaddress.ip_network(line, strict=True)
PY

{
  echo '# Generated from https://www.cloudflare.com/ips/ — do not edit manually.'
  echo 'real_ip_header CF-Connecting-IP;'
  echo 'real_ip_recursive on;'
  while IFS= read -r network; do
    printf 'set_real_ip_from %s;\n' "$network"
  done <"$TMP_DIR/ips-v4"
  while IFS= read -r network; do
    printf 'set_real_ip_from %s;\n' "$network"
  done <"$TMP_DIR/ips-v6"
} >"$TMP_DIR/cloudflare-realip.conf"

TARGET=/etc/nginx/conf.d/cloudflare-realip.conf
BACKUP="$TMP_DIR/cloudflare-realip.conf.previous"
if [[ -f "$TARGET" ]]; then
  cp "$TARGET" "$BACKUP"
fi
install -o root -g root -m 0644 "$TMP_DIR/cloudflare-realip.conf" "$TARGET"

if ! nginx -t; then
  if [[ -f "$BACKUP" ]]; then
    install -o root -g root -m 0644 "$BACKUP" "$TARGET"
  else
    rm -f "$TARGET"
  fi
  nginx -t
  echo "refusing Cloudflare real-IP configuration: nginx validation failed" >&2
  exit 1
fi

echo "installed trusted Cloudflare proxy ranges in $TARGET"
