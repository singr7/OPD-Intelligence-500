#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
require_sha "${1:-}"
REQUESTED_SHA="$1"
load_env
# `load_env` sources release.env, which carries the *last deployed* IMAGE_TAG.
# Assigning IMAGE_TAG before that call lets the old value silently overwrite the
# SHA the operator asked for, so hold the request in its own variable and restore
# it afterwards (activate-disposable-test.sh already did this; these two did not).
IMAGE_TAG="$REQUESTED_SHA"
export IMAGE_TAG
write_release_env "$IMAGE_TAG"

if [[ "$IMAGE_TAG" == "latest" ]]; then
  echo "mutable latest tags are forbidden" >&2
  exit 2
fi
prepare_release_images
compose up -d --wait postgres redis
compose stop api voice-gw worker beat web
set_database_read_only off
compose --profile migration run --rm migrate
if [[ "${OPD_WRITER_ENABLED:-0}" == "1" ]]; then
  set_database_read_only off
else
  set_database_read_only on
fi
compose up -d --wait postgres redis api voice-gw worker beat web

curl -fsS http://127.0.0.1:18080/health >/dev/null
curl -fsS http://127.0.0.1:13000/api/health >/dev/null

install -d -m 0750 "$RELEASES_DIR"
umask 027
printf '%s\n' "$IMAGE_TAG" >"$RELEASES_DIR/current-sha"

# Reference data from `seeds/*.json`, which nothing on this path ever loaded.
#
# A migration adds the *column*; only the seed adds the *row*. Doc 24's `AYUR`
# department shipped in `8fd588a` and was still absent from the console's
# Facility tab on 2026-08-12 — `4ce8cb36a165` had added `departments.care_system`
# to a table that never gained the department it exists for. There was no error
# to find: the deploy was correct, the images were correct, and the row simply
# had no way of arriving. Every future addition to `seeds/*.json` would have
# failed the same silent way.
#
# **Safe to re-run, by construction.** `_console_owned` in `app/seed.py` creates
# what is missing and refuses to overwrite a row an administrator can edit —
# the hospital, its departments, staff, doctors, clinic templates. A department
# closed from the console stays closed across this.
#
# `--patients 0` is not optional: the default generates 50 fake patients, and
# patients are deliberately *exempt* from that never-overwrite rule.
#
# Trees are seeded as drafts and are **not** published here. Publishing is a
# clinical act (doc 03 §3); the engine reads the on-disk bank regardless, so a
# department's intake works without this script asserting a review that has not
# happened.
#
# Writer boxes only. On a standby the database is read-only by the time we get
# here, and seeding it would make this box a second writer — which doc 17
# forbids outright.
#
# Runs last on purpose. The stack is already up, healthy, and recorded in
# `current-sha`, so a seed that fails costs reference data and an exit code,
# never an outage or a release this file cannot account for.
if [[ "${OPD_WRITER_ENABLED:-0}" == "1" ]]; then
  compose exec -T api python -m app.seed --patients 0
fi

echo "deployed $IMAGE_TAG; writer=$(writer_setting)"
