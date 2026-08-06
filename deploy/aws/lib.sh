#!/usr/bin/env bash
set -euo pipefail

OPD_ROOT="${OPD_ROOT:-/opt/opd/current}"
OPD_RUNTIME="${OPD_RUNTIME:-/opt/opd/runtime}"
OPD_DATA="${OPD_DATA:-/data}"
COMPOSE_FILE="${COMPOSE_FILE:-$OPD_ROOT/deploy/aws/compose.yml}"
APPLICATION_ENV="${APPLICATION_ENV:-$OPD_RUNTIME/application.env}"
WRITER_ENV="${WRITER_ENV:-$OPD_RUNTIME/writer.env}"
RELEASES_DIR="${RELEASES_DIR:-$OPD_RUNTIME/releases}"

compose() {
  docker compose --env-file "$APPLICATION_ENV" --env-file "$WRITER_ENV" \
    --env-file "$OPD_RUNTIME/release.env" -f "$COMPOSE_FILE" "$@"
}

# Where the scanned page images actually live on this host (doc 22 §2).
#
# Asked of Docker rather than hardcoded, because the two compose files mount
# /data/records differently — AWS binds the host directory, the local/Omen file
# uses a named volume whose real path is under /var/lib/docker/volumes. Reading
# it off the running container is correct for both and survives a rename.
#
# `$OPD_DATA/records` is the fallback for the case that matters most: a host
# where the stack is down and we are restoring onto it.
records_dir() {
  local cid src=""
  cid="$(compose ps -q api 2>/dev/null | head -n1 || true)"
  if [[ -n "$cid" ]]; then
    src="$(docker inspect \
      -f '{{range .Mounts}}{{if eq .Destination "/data/records"}}{{.Source}}{{end}}{{end}}' \
      "$cid" 2>/dev/null || true)"
  fi
  [[ -n "$src" ]] || src="${RECORDS_DIR:-$OPD_DATA/records}"
  printf '%s\n' "$src"
}

# Scanned pages are **append-only**: `page_key()` builds one deterministic key
# per (patient, document, page), nothing rewrites a key, and no code path
# deletes one. So the pages are backed up as an incremental sync into a single
# shared prefix rather than a tarball per backup — a 15-minute tar of a
# directory growing by gigabytes a week would be unusable within a month, and
# per-backup copies would multiply those gigabytes for bytes that never change.
#
# Deliberately no `--delete`. A restore of an older database must still find its
# pages, and nothing on the box is entitled to remove a patient's scanned report
# from the backup as a side effect of a sync.
RECORDS_PREFIX="pages"

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "run as root" >&2
    exit 1
  fi
}

require_sha() {
  if [[ ! "${1:-}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "expected a full 40-character lowercase Git SHA" >&2
    exit 2
  fi
}

normalize_nginx_http2_syntax() {
  local config_file="$1"
  local nginx_version
  nginx_version="$(nginx -v 2>&1)"
  nginx_version="${nginx_version#nginx version: nginx/}"
  if dpkg --compare-versions "$nginx_version" ge 1.25.1; then
    sed -i \
      -e 's/listen 443 ssl http2;/listen 443 ssl;/' \
      -e 's/listen \[::\]:443 ssl http2;/listen [::]:443 ssl;/' \
      "$config_file"
    sed -i '/listen \[::\]:443 ssl;/a\    http2 on;' "$config_file"
  fi
}

load_env() {
  if [[ ! -r "$APPLICATION_ENV" || ! -r "$WRITER_ENV" ]]; then
    echo "runtime environment files are missing" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$APPLICATION_ENV"
  # shellcheck disable=SC1090
  source "$WRITER_ENV"
  if [[ -r "$OPD_RUNTIME/release.env" ]]; then
    # shellcheck disable=SC1090,SC1091
    source "$OPD_RUNTIME/release.env"
  fi
  set +a
  if [[ ! "${POSTGRES_USER:-opd}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] ||
    [[ ! "${POSTGRES_DB:-opd}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
    echo "POSTGRES_USER and POSTGRES_DB must be simple PostgreSQL identifiers" >&2
    exit 2
  fi
  OPD_IMAGE_SOURCE="${OPD_IMAGE_SOURCE:-ecr}"
  if [[ "$OPD_IMAGE_SOURCE" != "ecr" && "$OPD_IMAGE_SOURCE" != "local" ]]; then
    echo "OPD_IMAGE_SOURCE must be ecr or local" >&2
    exit 2
  fi
  export OPD_IMAGE_SOURCE
  IMAGE_TAG="${IMAGE_TAG:-${RELEASE_SHA:-}}"
  if [[ -n "$IMAGE_TAG" ]]; then
    require_sha "$IMAGE_TAG"
    export IMAGE_TAG
  fi
}

prepare_release_images() {
  : "${ECR_REGISTRY:?set ECR_REGISTRY image namespace}"
  case "$OPD_IMAGE_SOURCE" in
    ecr)
      : "${AWS_REGION:?set AWS_REGION}"
      aws ecr get-login-password --region "$AWS_REGION" |
        docker login --username AWS --password-stdin "$ECR_REGISTRY"
      compose pull api voice-gw worker beat web
      ;;
    local)
      local image
      for image in api voice-gw worker web; do
        docker image inspect "$ECR_REGISTRY/opd-$image:$IMAGE_TAG" >/dev/null || {
          echo "missing local release image: $ECR_REGISTRY/opd-$image:$IMAGE_TAG" >&2
          exit 3
        }
      done
      ;;
  esac
}

writer_setting() {
  compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
    -tAc "show default_transaction_read_only" | tr -d '[:space:]'
}

assert_not_writer() {
  if [[ "$(writer_setting)" != "on" ]]; then
    echo "refusing: target database is a live writer" >&2
    exit 3
  fi
}

write_writer_env() {
  local enabled="$1"
  umask 077
  printf 'OPD_WRITER_ENABLED=%s\n' "$enabled" >"$WRITER_ENV.tmp"
  chown root:root "$WRITER_ENV.tmp"
  chmod 0600 "$WRITER_ENV.tmp"
  mv "$WRITER_ENV.tmp" "$WRITER_ENV"
}

write_release_env() {
  local sha="$1"
  require_sha "$sha"
  umask 077
  {
    printf 'IMAGE_TAG=%s\n' "$sha"
    printf 'RELEASE_SHA=%s\n' "$sha"
  } >"$OPD_RUNTIME/release.env.tmp"
  chown root:root "$OPD_RUNTIME/release.env.tmp"
  chmod 0600 "$OPD_RUNTIME/release.env.tmp"
  mv "$OPD_RUNTIME/release.env.tmp" "$OPD_RUNTIME/release.env"
}

set_database_read_only() {
  local value="$1"
  if [[ "$value" != "on" && "$value" != "off" ]]; then
    echo "read-only value must be on or off" >&2
    exit 2
  fi
  compose exec -T -e PGOPTIONS="-c default_transaction_read_only=off" postgres \
    psql -U "${POSTGRES_USER:-opd}" -d postgres -v ON_ERROR_STOP=1 \
    -c "alter role \"${POSTGRES_USER:-opd}\" set default_transaction_read_only = $value"
}
