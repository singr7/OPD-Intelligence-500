from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("release_manifest.py")
SPEC = importlib.util.spec_from_file_location("release_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
release_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_manifest)


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_contains_update_identity_and_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apk = root / "opd-patient.apk"
            apk.write_bytes(b"signed-apk-fixture")
            output = root / "manifest.json"
            argv = [
                "release_manifest.py",
                "--output", str(output),
                "--version-code", "2",
                "--version-name", "1.0.0-android1",
                "--release-sha", "a" * 40,
                "--apk", str(apk),
                "--checksum", "b" * 64,
                "--certificate", "C" * 64,
            ]
            with patch("sys.argv", argv):
                self.assertEqual(release_manifest.main(), 0)
            manifest = json.loads(output.read_text())
            self.assertEqual(manifest["package_id"], "ai.radpretation.opd")
            self.assertEqual(manifest["version_code"], 2)
            self.assertEqual(manifest["min_sdk"], 26)
            self.assertEqual(manifest["size_bytes"], len(b"signed-apk-fixture"))
            self.assertEqual(manifest["certificate_sha256"], "c" * 64)


if __name__ == "__main__":
    unittest.main()
