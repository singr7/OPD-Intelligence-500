from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("secret-to-env.py")
SPEC = importlib.util.spec_from_file_location("secret_to_env", MODULE_PATH)
assert SPEC and SPEC.loader
secret_to_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secret_to_env)


class SecretToEnvTests(unittest.TestCase):
    def payload(self) -> dict[str, str]:
        return {
            "POSTGRES_PASSWORD": "database password",
            "JWT_SECRET": "j" * 32,
            "SECRETS_KEY": 'quotes-" dollars-$',
            "AWS_REGION": "ap-south-1",
            "ECR_REGISTRY": "000000000000.dkr.ecr.ap-south-1.amazonaws.com",
            "BACKUP_BUCKET": "opd-test-backups",
            "PUBLIC_HOSTNAME": "aws.opd.example.invalid",
            "ENVIRONMENT_ID": "aws",
            "ENVIRONMENT_NAME": "AWS standby",
        }

    def convert(self, payload: dict[str, str]) -> tuple[int, str, int]:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "secret.json")
            destination = Path(directory, "application.env")
            source.write_text(json.dumps(payload))
            with patch.object(os.sys, "argv", ["secret-to-env.py", str(source), str(destination)]):
                result = secret_to_env.main()
            return result, destination.read_text(), destination.stat().st_mode & 0o777

    def test_outputs_sorted_root_readable_env_without_plain_dollar_expansion(self) -> None:
        result, output, mode = self.convert(self.payload())
        self.assertEqual(result, 0)
        self.assertEqual(mode, 0o600)
        self.assertIn('SECRETS_KEY="quotes-\\" dollars-$$"', output)
        self.assertEqual(output.splitlines(), sorted(output.splitlines()))

    def test_rejects_unknown_fields(self) -> None:
        payload = self.payload() | {"UNREVIEWED_SECRET": "no"}
        with self.assertRaisesRegex(ValueError, "unknown secret fields"):
            self.convert(payload)

    def test_rejects_empty_required_field(self) -> None:
        payload = self.payload() | {"JWT_SECRET": ""}
        with self.assertRaisesRegex(ValueError, "may not be empty"):
            self.convert(payload)

    def test_rejects_multiline_value(self) -> None:
        payload = self.payload() | {"OPENAI_API_KEY": "line1\nline2"}
        with self.assertRaisesRegex(ValueError, "line breaks"):
            self.convert(payload)


if __name__ == "__main__":
    unittest.main()
