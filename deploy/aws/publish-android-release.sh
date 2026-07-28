#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/aws/lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
if [[ $# -ne 3 ]]; then
  echo "usage: publish-android-release.sh <apk> <manifest.json> <checksum.sha256>" >&2
  exit 2
fi
APK="$(realpath "$1")"
MANIFEST="$(realpath "$2")"
CHECKSUM_FILE="$(realpath "$3")"
for file in "$APK" "$MANIFEST" "$CHECKSUM_FILE"; do
  [[ -f "$file" ]]
done
command -v apksigner >/dev/null || {
  echo "apksigner is required to verify the transferred artifact" >&2
  exit 3
}

readarray -t META < <(python3 - "$MANIFEST" <<'PY'
import json, re, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
required = {"package_id", "version_code", "version_name", "min_sdk", "release_sha",
            "size_bytes", "certificate_sha256", "sha256", "artifact"}
if set(data) < required:
    raise SystemExit("manifest fields are incomplete")
if data["package_id"] != "ai.radpretation.opd" or data["min_sdk"] != 26:
    raise SystemExit("unexpected Android identity")
if not re.fullmatch(r"[0-9a-f]{40}", data["release_sha"]):
    raise SystemExit("release_sha is not a full Git SHA")
if not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
    raise SystemExit("checksum is malformed")
if not re.fullmatch(r"[0-9a-f]{64}", data["certificate_sha256"]):
    raise SystemExit("certificate fingerprint is malformed")
print(data["version_name"])
print(data["sha256"])
print(data["certificate_sha256"])
print(data["size_bytes"])
PY
)
VERSION="${META[0]}"
EXPECTED_SHA="${META[1]}"
EXPECTED_CERT="${META[2]}"
EXPECTED_SIZE="${META[3]}"
[[ "$VERSION" =~ ^[0-9A-Za-z._-]+$ ]]
[[ "$(wc -c <"$APK" | tr -d '[:space:]')" == "$EXPECTED_SIZE" ]]
[[ "$(sha256sum "$APK" | awk '{print $1}')" == "$EXPECTED_SHA" ]]
grep -q "^$EXPECTED_SHA  " "$CHECKSUM_FILE"

VERIFY_REPORT="$(mktemp)"
trap 'rm -f "$VERIFY_REPORT"' EXIT
apksigner verify --verbose --print-certs "$APK" >"$VERIFY_REPORT"
ACTUAL_CERT="$(sed -n 's/^Signer #1 certificate SHA-256 digest: //p' "$VERIFY_REPORT" |
  head -1 | tr '[:upper:]' '[:lower:]' | tr -d ':')"
[[ "$ACTUAL_CERT" == "$EXPECTED_CERT" ]]

STAGING="$OPD_DATA/releases/.staging-$VERSION-$$"
install -d -m 0755 "$OPD_DATA/releases" "$STAGING"
install -m 0644 "$APK" "$STAGING/opd-patient-$VERSION.apk"
install -m 0644 "$MANIFEST" "$STAGING/opd-patient-$VERSION.json"
install -m 0644 "$CHECKSUM_FILE" "$STAGING/opd-patient-$VERSION.sha256"
mv "$STAGING"/* "$OPD_DATA/releases/"
rmdir "$STAGING"

for extension in apk json sha256; do
  LINK_TMP="$OPD_DATA/releases/.opd-patient-latest.$extension.$$"
  ln -s "opd-patient-$VERSION.$extension" "$LINK_TMP"
  mv -Tf "$LINK_TMP" "$OPD_DATA/releases/opd-patient-latest.$extension"
done
echo "published byte identity $EXPECTED_SHA as $VERSION"
