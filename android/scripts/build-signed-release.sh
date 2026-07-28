#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ANDROID_ROOT/.." && pwd)"
cd "$REPO_ROOT"

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "refusing: signed releases must come from a clean commit" >&2
  exit 2
fi
: "${OPD_SIGNING_KEYSTORE:?set OPD_SIGNING_KEYSTORE}"
: "${OPD_SIGNING_STORE_PASSWORD:?set OPD_SIGNING_STORE_PASSWORD}"
: "${OPD_SIGNING_KEY_ALIAS:?set OPD_SIGNING_KEY_ALIAS}"
: "${OPD_SIGNING_KEY_PASSWORD:?set OPD_SIGNING_KEY_PASSWORD}"
: "${OPD_OMEN_API_BASE:?set OPD_OMEN_API_BASE}"
: "${OPD_AWS_API_BASE:?set OPD_AWS_API_BASE}"

RELEASE_SHA="$(git rev-parse HEAD)"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]
export RELEASE_SHA
"$ANDROID_ROOT/gradlew" -p "$ANDROID_ROOT" --no-daemon --no-parallel \
  clean testReleaseUnitTest assembleRelease checkApkSize

APK="$ANDROID_ROOT/app/build/outputs/apk/release/app-release.apk"
[[ -f "$APK" ]]
SDK_ROOT="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
: "${SDK_ROOT:?set ANDROID_SDK_ROOT or ANDROID_HOME}"
BUILD_TOOLS="$(find "$SDK_ROOT/build-tools" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
APKSIGNER="$BUILD_TOOLS/apksigner"
[[ -x "$APKSIGNER" ]]

OUTPUT_DIR="${OPD_RELEASE_OUTPUT:-$ANDROID_ROOT/release}"
install -d -m 0750 "$OUTPUT_DIR"
VERSION="1.0.1-demo1"
OUT_APK="$OUTPUT_DIR/opd-patient-$VERSION.apk"
cp "$APK" "$OUT_APK"
"$APKSIGNER" verify --verbose --print-certs "$OUT_APK" >"$OUTPUT_DIR/apksigner-report.txt"
CHECKSUM="$(shasum -a 256 "$OUT_APK" | awk '{print $1}')"
printf '%s  %s\n' "$CHECKSUM" "$(basename "$OUT_APK")" >"$OUTPUT_DIR/opd-patient-$VERSION.sha256"
CERTIFICATE="$(sed -n 's/^Signer #1 certificate SHA-256 digest: //p' "$OUTPUT_DIR/apksigner-report.txt" | head -1)"
[[ "$CERTIFICATE" =~ ^[0-9a-fA-F]{64}$ ]]
python3 "$SCRIPT_DIR/release_manifest.py" \
  --output "$OUTPUT_DIR/opd-patient-$VERSION.json" \
  --version-code 3 \
  --version-name "$VERSION" \
  --release-sha "$RELEASE_SHA" \
  --apk "$OUT_APK" \
  --checksum "$CHECKSUM" \
  --certificate "$CERTIFICATE"
echo "$OUTPUT_DIR"
