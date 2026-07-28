#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
VERSION="${1:-}"
[[ "$VERSION" =~ ^[0-9A-Za-z._-]+$ ]] || {
  echo "usage: rollback-android-release.sh <retained-version>" >&2
  exit 2
}
for extension in apk json sha256; do
  TARGET="$OPD_DATA/releases/opd-patient-$VERSION.$extension"
  [[ -f "$TARGET" ]] || {
    echo "retained artifact is incomplete: $TARGET" >&2
    exit 3
  }
done
for extension in apk json sha256; do
  LINK_TMP="$OPD_DATA/releases/.opd-patient-latest.$extension.$$"
  ln -s "opd-patient-$VERSION.$extension" "$LINK_TMP"
  mv -Tf "$LINK_TMP" "$OPD_DATA/releases/opd-patient-latest.$extension"
done
echo "download links rolled back to retained version $VERSION; no artifact deleted"
