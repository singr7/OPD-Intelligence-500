#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--checksum", required=True)
    parser.add_argument("--certificate", required=True)
    args = parser.parse_args()
    payload = {
        "schema": 1,
        "package_id": "ai.radpretation.opd",
        "version_code": args.version_code,
        "version_name": args.version_name,
        "min_sdk": 26,
        "release_sha": args.release_sha,
        "size_bytes": args.apk.stat().st_size,
        "certificate_sha256": args.certificate.lower().replace(":", ""),
        "sha256": args.checksum.lower(),
        "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifact": args.apk.name,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
