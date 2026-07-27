#!/usr/bin/env bash
# Take a restore point on the pilot box, before pulling new code.
#
# Three things get saved, because a bad deploy can go wrong in three ways:
#
#   1. the commit that is running       -> a git tag + a file
#   2. the images that are running      -> retagged, so rollback is a retag and
#                                          not a rebuild (rebuilds can fail, and
#                                          a rollback that needs a working build
#                                          is not a rollback)
#   3. the database                     -> pg_dump, gzipped
#
# Writes everything to ~/opd-checkpoints/<stamp>/ and prints the rollback
# command. Reads nothing, changes no running container.
#
# Usage:  ./deploy/omen-checkpoint.sh          (run from the repo root on the box)

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${OPD_CHECKPOINT_DIR:-$HOME/opd-checkpoints}/$STAMP"
SERVICES="api web worker beat voice-gw"

mkdir -p "$OUT"

echo "==> Checkpoint $STAMP  ->  $OUT"

# -- 1. the commit -------------------------------------------------------------

COMMIT="$(git rev-parse HEAD)"
git rev-parse --abbrev-ref HEAD > "$OUT/branch"
echo "$COMMIT" > "$OUT/commit"
git tag -f "omen-checkpoint-$STAMP" "$COMMIT" >/dev/null
echo "    commit  $COMMIT  (tag omen-checkpoint-$STAMP)"

# Uncommitted local changes are a real thing on a box somebody has poked at.
# Save them rather than discover them during a rollback.
if ! git diff --quiet || ! git diff --cached --quiet; then
  git diff HEAD > "$OUT/local-changes.patch"
  echo "    ⚠ uncommitted changes saved to local-changes.patch"
fi

# -- 2. the images -------------------------------------------------------------
#
# By ID, not by name: compose names images <project>-<service>, and hard-coding
# that assumption is how a rollback script fails on the one box it exists for.

: > "$OUT/images"
for svc in $SERVICES; do
  id="$(docker compose images -q "$svc" 2>/dev/null | head -1 || true)"
  # The repo:tag compose is currently using for this service, read from compose
  # itself rather than reconstructed from the project name — compose lowercases
  # the directory name, and guessing that is how this fails on the one box it
  # exists for. Recorded now, while it is knowable, so the rollback does not
  # have to work it out later.
  name="$(docker compose images "$svc" 2>/dev/null | awk 'NR==2 && $2!="" {print $2":"$3}')"
  if [ -n "$id" ] && [ -n "$name" ]; then
    docker tag "$id" "opd-rollback/$svc:$STAMP"
    echo "$svc $id $name" >> "$OUT/images"
    echo "    image   $svc -> opd-rollback/$svc:$STAMP  ($name)"
  else
    echo "    image   $svc -> (not built/running, skipped)"
  fi
done

# -- 3. the database -----------------------------------------------------------

echo "    dumping postgres…"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
  | gzip > "$OUT/opd.sql.gz"
echo "    db      $(du -h "$OUT/opd.sql.gz" | cut -f1)  -> opd.sql.gz"

# The alembic revision, so a rollback knows what it is going back to.
docker compose exec -T postgres psql -U "${POSTGRES_USER:-opd}" -d "${POSTGRES_DB:-opd}" \
  -tAc "select version_num from alembic_version" > "$OUT/alembic_version" 2>/dev/null || true
echo "    schema  $(cat "$OUT/alembic_version" 2>/dev/null || echo unknown)"

cp .env "$OUT/env.backup" 2>/dev/null && echo "    .env    saved" || echo "    ⚠ no .env found"

echo
echo "==> Checkpoint complete."
echo "    Roll back with:  ./deploy/omen-rollback.sh $STAMP"
