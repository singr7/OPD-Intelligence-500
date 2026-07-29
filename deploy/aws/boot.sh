#!/usr/bin/env bash
# Bring the deployed release back up after an instance start, and reconcile the
# database's writer setting with what `writer.env` says it should be.
#
# ## Why this exists at all
#
# Most of a reboot already takes care of itself: every service carries
# `restart: unless-stopped`, and the writer setting is `ALTER ROLE ... SET
# default_transaction_read_only`, which lives in the database and survives a
# restart. What does *not* take care of itself is the handful of ways a boot can
# come up half-right, and each one is quiet:
#
#   * `/data` (the EBS volume holding PostgreSQL) not mounted before Docker
#     starts — PostgreSQL then initialises an empty database on the root disk and
#     looks perfectly healthy while serving nothing. This script refuses to start
#     rather than let that happen.
#   * a container that was explicitly `stop`ped before the reboot (a failed
#     deploy, an interrupted activation) — `unless-stopped` deliberately does not
#     bring those back.
#   * a writer setting that disagrees with `writer.env`, because someone changed
#     one and not the other.
#
# ## What it deliberately does NOT do
#
# **It does not force write mode.** It reads `OPD_WRITER_ENABLED` from
# `writer.env` and makes the database match. On a disposable test box that file
# says 1 and the box comes up writable; on a read-only standby it says 0 and the
# box comes up read-only. A boot script that hardcoded "writable" would silently
# promote a standby to a writer every time the instance restarted, which is the
# one thing the whole single-writer boundary exists to prevent.
#
# **It does not run migrations.** Schema changes belong to `deploy.sh`, run by a
# human who chose the release. A boot that migrates is a boot that can fail
# halfway through a schema change with nobody watching.
#
# **It does not choose a release.** `IMAGE_TAG` comes from `release.env`, so this
# starts exactly what was last deployed and never a `latest`.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
load_env

: "${IMAGE_TAG:?release.env has no IMAGE_TAG — nothing has been deployed on this host}"

# The data volume must be a real mount before PostgreSQL touches it. Without this
# guard a missing volume produces an empty, healthy-looking database.
if ! mountpoint -q "$OPD_DATA"; then
  echo "refusing to start: $OPD_DATA is not a mounted filesystem" >&2
  exit 3
fi

echo "starting release $IMAGE_TAG (writer.env says OPD_WRITER_ENABLED=${OPD_WRITER_ENABLED:-0})"

# Data services first, so the writer reconciliation below has a database to talk
# to before anything can serve a request against the wrong setting.
compose up -d --wait postgres redis

# Reconcile the database with writer.env. Idempotent: `ALTER ROLE ... SET` to the
# value it already holds is a no-op.
if [[ "${OPD_WRITER_ENABLED:-0}" == "1" ]]; then
  set_database_read_only off
else
  set_database_read_only on
fi

compose up -d --wait api voice-gw worker beat web

# Liveness, from the box itself. Public HTTPS is deliberately not checked here:
# at boot, DNS and any proxy in front may not be ready, and a boot unit that
# fails on somebody else's DNS teaches operators to ignore it.
curl -fsS http://127.0.0.1:18080/health >/dev/null
curl -fsS http://127.0.0.1:13000/api/health >/dev/null

MODE="read-only standby"
[[ "$(writer_setting)" == "off" ]] && MODE="WRITABLE"
echo "release $IMAGE_TAG is up; database is $MODE"
