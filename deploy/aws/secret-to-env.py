#!/usr/bin/env python3
"""Convert the allow-listed Secrets Manager JSON object to a Compose env file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ALLOWED = {
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_URL",
    "JWT_SECRET",
    "SECRETS_KEY",
    "OPENAI_API_KEY",
    "SARVAM_API_KEY",
    "MSG91_AUTH_KEY",
    "EXOTEL_API_KEY",
    "EXOTEL_API_TOKEN",
    "EXOTEL_ACCOUNT_SID",
    "EXOTEL_WEBHOOK_TOKEN",
    "META_WHATSAPP_TOKEN",
    "META_PHONE_NUMBER_ID",
    "META_VERIFY_TOKEN",
    "META_APP_SECRET",
    "AWS_REGION",
    "ECR_REGISTRY",
    "BACKUP_BUCKET",
    "PUBLIC_HOSTNAME",
    "ENVIRONMENT_ID",
    "ENVIRONMENT_NAME",
}
REQUIRED = {
    "POSTGRES_PASSWORD",
    "JWT_SECRET",
    "SECRETS_KEY",
    "AWS_REGION",
    "ECR_REGISTRY",
    "BACKUP_BUCKET",
    "PUBLIC_HOSTNAME",
    "ENVIRONMENT_ID",
    "ENVIRONMENT_NAME",
}


def quote(value: str) -> str:
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("secret values may not contain line breaks or NUL")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: secret-to-env.py <secret.json> <application.env>")
    source, destination = map(Path, sys.argv[1:])
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("secret must be a JSON object")
    unknown = set(payload) - ALLOWED
    missing = REQUIRED - set(payload)
    if unknown:
        raise ValueError(f"unknown secret fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing secret fields: {sorted(missing)}")
    if any(not isinstance(value, str) for value in payload.values()):
        raise ValueError("every secret field must be a string")
    empty = {key for key in REQUIRED if not payload[key]}
    if empty:
        raise ValueError(f"required secret fields may not be empty: {sorted(empty)}")
    destination.write_text(
        "".join(f"{key}={quote(payload[key])}\n" for key in sorted(payload)),
        encoding="utf-8",
    )
    destination.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
