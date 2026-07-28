#!/usr/bin/env python3
"""Validate and finalize a non-PHI manual failover/failback drill record."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REQUIRED_TEXT = {
    "source_environment",
    "target_environment",
    "stable_alias",
    "source_commit",
    "target_commit",
    "known_intake_id",
    "backup_id",
    "quiesced_at",
    "backup_cutoff_at",
    "restore_completed_at",
    "promoted_at",
    "public_health_passed_at",
    "failback_completed_at",
    "post_cutoff_intake_id",
}


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def finalize(record: dict[str, object]) -> dict[str, object]:
    missing = sorted(key for key in REQUIRED_TEXT if not record.get(key))
    if missing:
        raise ValueError(f"missing drill evidence: {missing}")
    if record.get("source_and_target_never_concurrent_writers") is not True:
        raise ValueError("concurrent-writer exclusion was not affirmed")
    if record.get("post_cutoff_intake_absent_on_target") is not True:
        raise ValueError("post-cutoff intake boundary was not verified")
    quiesced = instant(str(record["quiesced_at"]))
    cutoff = instant(str(record["backup_cutoff_at"]))
    healthy = instant(str(record["public_health_passed_at"]))
    failback = instant(str(record["failback_completed_at"]))
    rpo = int((quiesced - cutoff).total_seconds())
    rto = int((healthy - quiesced).total_seconds())
    if rpo < 0 or rto < 0 or failback < healthy:
        raise ValueError("drill timestamps are out of order")
    return record | {
        "rpo_seconds": rpo,
        "rto_seconds": rto,
        "rpo_target_seconds": 900,
        "rto_target_seconds": 1800,
        "rpo_target_met": rpo <= 900,
        "rto_target_met": rto <= 1800,
        "status": "completed",
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: drill-report.py <record.json> <final-report.json>")
    source, destination = map(Path, sys.argv[1:])
    report = finalize(json.loads(source.read_text(encoding="utf-8")))
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
