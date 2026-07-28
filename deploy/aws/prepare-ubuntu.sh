#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
if [[ $# -ne 1 || ! "$1" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "usage: prepare-ubuntu.sh <public-hostname>" >&2
  exit 2
fi
PUBLIC_HOSTNAME="$1"

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "this preparation path supports Ubuntu only" >&2
  exit 2
fi

case "$(dpkg --print-architecture)" in
  amd64 | arm64) ;;
  *)
    echo "supported architectures are amd64 and arm64" >&2
    exit 2
    ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg git jq nginx certbot openssl \
  python3 python3-certbot-nginx unzip awscli

if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
    gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
    "$(dpkg --print-architecture)" "$VERSION_CODENAME" \
    >/etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
fi

systemctl enable --now docker nginx
docker compose version
aws --version
nginx -v

DATA_TARGET="$(findmnt -n -T /data -o TARGET 2>/dev/null || true)"
if [[ "$DATA_TARGET" != "/data" ]]; then
  echo "warning: /data is not a dedicated mount; confirm the root EBS volume is encrypted" >&2
fi

"$SCRIPT_DIR/bootstrap.sh" "$PUBLIC_HOSTNAME"

echo
echo "Ubuntu host preparation complete for $PUBLIC_HOSTNAME."
echo "Next: create the root-only runtime env, build/deploy a full-SHA release, point DNS, then enable TLS."
