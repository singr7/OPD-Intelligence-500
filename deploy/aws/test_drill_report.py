from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("drill-report.py")
SPEC = importlib.util.spec_from_file_location("drill_report", MODULE_PATH)
assert SPEC and SPEC.loader
drill_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill_report)


def valid_record() -> dict[str, object]:
    return {
        "source_environment": "https://omen.example",
        "target_environment": "https://aws.example",
        "stable_alias": "https://opd.example",
        "source_commit": "a" * 40,
        "target_commit": "b" * 40,
        "known_intake_id": "non-phi-1",
        "backup_id": "20260728T010000Z",
        "backup_cutoff_at": "2026-07-28T01:00:00Z",
        "quiesced_at": "2026-07-28T01:10:00Z",
        "restore_completed_at": "2026-07-28T01:18:00Z",
        "promoted_at": "2026-07-28T01:20:00Z",
        "public_health_passed_at": "2026-07-28T01:25:00Z",
        "failback_completed_at": "2026-07-28T02:00:00Z",
        "post_cutoff_intake_id": "non-phi-2",
        "post_cutoff_intake_absent_on_target": True,
        "source_and_target_never_concurrent_writers": True,
    }


class DrillReportTests(unittest.TestCase):
    def test_computes_measured_rpo_and_rto(self) -> None:
        report = drill_report.finalize(valid_record())
        self.assertEqual(report["rpo_seconds"], 600)
        self.assertEqual(report["rto_seconds"], 900)
        self.assertTrue(report["rpo_target_met"])
        self.assertTrue(report["rto_target_met"])

    def test_records_a_missed_target_instead_of_claiming_success(self) -> None:
        record = valid_record() | {"public_health_passed_at": "2026-07-28T01:45:01Z"}
        report = drill_report.finalize(record)
        self.assertFalse(report["rto_target_met"])
        self.assertEqual(report["rto_seconds"], 2101)

    def test_refuses_unproven_cutoff_boundary(self) -> None:
        record = valid_record() | {"post_cutoff_intake_absent_on_target": False}
        with self.assertRaisesRegex(ValueError, "boundary"):
            drill_report.finalize(record)

    def test_refuses_concurrent_writer_ambiguity(self) -> None:
        record = valid_record() | {"source_and_target_never_concurrent_writers": False}
        with self.assertRaisesRegex(ValueError, "concurrent-writer"):
            drill_report.finalize(record)


if __name__ == "__main__":
    unittest.main()
