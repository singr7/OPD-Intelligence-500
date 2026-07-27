#!/usr/bin/env bash
# Go back to a checkpoint taken by omen-checkpoint.sh.
#
# Default is a **code + image** rollback, which is what you almost always want:
#
#   - the S-GL.1 migration is purely additive (two new tables, no ALTER on
#     anything that existed), so old code runs perfectly happily against the new
#     schema — it simply never looks at those tables. Restoring the database is
#     therefore NOT part of a normal rollback, and doing it would throw away
#     every intake, token and consult note recorded since the checkpoint.
#
# The database is restored only if you ask for it explicitly, and it asks you to
# type the word out.
#
# Usage:
#   ./deploy/omen-rollback.sh <stamp>              code + images  (safe, default)
#   ./deploy/omen-rollback.sh <stamp> --with-db    …and overwrite the database
#   ./deploy/omen-rollback.sh --list

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

BASE="${OPD_CHECKPOINT_DIR:-$HOME/opd-checkpoints}"
SERVICES="api web worker beat voice-gw"

if [ "${1:-}" = "--list" ] || [ $# -eq 0 ]; then
  echo "Checkpoints in $BASE:"
  ls -1 "$BASE" 2>/dev/null | sed 's/^/  /' || echo "  (none)"
  echo
  echo "Usage: $0 <stamp> [--with-db]"
  exit 0
fi

STAMP="$1"
OUT="$BASE/$STAMP"
WITH_DB="${2:-}"

[ -d "$OUT" ] || { echo "No checkpoint $STAMP in $BASE"; exit 1; }

COMMIT="$(cat "$OUT/commit")"
BRANCH="$(cat "$OUT/branch" 2>/dev/null || echo main)"

echo "==> Rolling back to $STAMP"
echo "    commit $COMMIT on $BRANCH"

# -- 1. code -------------------------------------------------------------------

git checkout -q "$COMMIT"
echo "    code   at $COMMIT (detached HEAD — this is fine and is the point)"

if [ -f "$OUT/local-changes.patch" ]; then
  echo "    ⚠ the checkpoint had uncommitted changes: $OUT/local-changes.patch"
  echo "      re-apply by hand if they mattered (git apply <path>)"
fi

# -- 2. images -----------------------------------------------------------------
#
# Retagged, never rebuilt. A rollback that depends on a successful build is not
# a rollback — the build is often the thing that broke.

RESTORED=0
while read -r svc id name; do
  [ -n "${svc:-}" ] && [ -n "${name:-}" ] || continue
  if docker image inspect "opd-rollback/$svc:$STAMP" >/dev/null 2>&1; then
    # Retag the saved image back onto the exact repo:tag the checkpoint recorded
    # compose was using.
    docker tag "opd-rollback/$svc:$STAMP" "$name"
    echo "    image  $svc <- opd-rollback/$svc:$STAMP  ($name)"
    RESTORED=$((RESTORED + 1))
  fi
done < "$OUT/images"
[ "$RESTORED" -gt 0 ] || echo "    ⚠ no saved images found — containers will rebuild from the restored code"

# -- 3. the database, only if asked --------------------------------------------

if [ "$WITH_DB" = "--with-db" ]; then
  echo
  echo "    ⚠ RESTORING THE DATABASE discards every intake, token, queue entry,"
  echo "      consult note and prescription created since $STAMP."
  printf "      Type RESTORE to confirm: "
  read -r answer
  if [ "$answer" = "RESTORE" ]; then
    docker compose stop api worker beat voice-gw
    gunzip -c "$OUT/opd.sql.gz" | docker compose exec -T postgres \
      psql -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}"
    echo "    db     restored from $OUT/opd.sql.gz"
  else
    echo "    db     left alone (you did not type RESTORE)"
  fi
else
  echo "    db     left alone — the added tables are inert to the old code."
  echo "           Add --with-db only if the data itself is wrong."
fi

# -- 4. back up ----------------------------------------------------------------
#
# `up -d`, never `down`: `down` removes the opd_default network and disconnects
# opd-vllm / opd-stt (doc 10 §2).

echo
echo "==> Restarting services (no 'down' — the GPU containers stay attached)"
docker compose up -d

sleep 5
docker compose ps
echo
curl -fsS "http://localhost:${API_HOST_PORT:-18080}/health" && echo "  api ok" \
  || echo "  ⚠ api not answering yet — docker compose logs -f api"
echo
echo "==> Rolled back to $STAMP."
echo "    To return to the tip afterwards:  git checkout $BRANCH && docker compose build && docker compose up -d"
