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
  docker compose --env-file "$APPLICATION_ENV" --env-file "$WRITER_ENV" -f "$COMPOSE_FILE" "$@"
}

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
  set +a
  if [[ ! "${POSTGRES_USER:-opd}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] ||
    [[ ! "${POSTGRES_DB:-opd}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
    echo "POSTGRES_USER and POSTGRES_DB must be simple PostgreSQL identifiers" >&2
    exit 2
  fi
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
