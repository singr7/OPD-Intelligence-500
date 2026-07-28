#!/usr/bin/env bash
set -euo pipefail
NAME="opd-writer-lock-test-$$"
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker run -d --name "$NAME" \
  -e POSTGRES_PASSWORD=test-only -e POSTGRES_USER=opd -e POSTGRES_DB=opd \
  postgres:16 >/dev/null
for _ in $(seq 1 30); do
  docker exec "$NAME" pg_isready -U opd -d opd >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$NAME" psql -U opd -d postgres -v ON_ERROR_STOP=1 \
  -c 'alter role opd set default_transaction_read_only = on' >/dev/null
if docker exec "$NAME" psql -U opd -d opd -v ON_ERROR_STOP=1 \
  -c 'create table forbidden_write(id int)' >/dev/null 2>&1; then
  echo "read-only role unexpectedly accepted a write" >&2
  exit 1
fi
docker exec -e PGOPTIONS='-c default_transaction_read_only=off' "$NAME" \
  psql -U opd -d postgres -v ON_ERROR_STOP=1 \
  -c 'alter role opd set default_transaction_read_only = off' >/dev/null
docker exec "$NAME" psql -U opd -d opd -v ON_ERROR_STOP=1 \
  -c 'create table allowed_after_promotion(id int)' >/dev/null
echo "database writer lock: ok"
