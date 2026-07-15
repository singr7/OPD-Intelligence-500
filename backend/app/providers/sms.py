"""SMSProvider interface + the fake (doc 02 §9).

Hard rule from doc 02 §9: feature code never touches a vendor SDK. The OTP flow
talks to `SMSProvider` only, so choosing MSG91 vs Exotel in S3 is a config change
and nothing above this line moves.

S2 ships the interface and the fake. Two things land in S3 alongside the real
implementations:
  - usage_events metering on every send (a provider without metering fails review)
  - retry / circuit-breaker + the provider health registry
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmsMessage:
    to: str
    body: str
    # Registered template id — India DLT requires one for transactional SMS.
    template_key: str | None = None


@dataclass(frozen=True)
class SmsResult:
    provider: str
    message_id: str
    accepted: bool
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SMSProvider(ABC):
    """Transactional SMS (OTP today; Rx delivery + check-in fallback in S11/S17)."""

    name: str = "abstract"

    @abstractmethod
    async def send(self, message: SmsMessage) -> SmsResult:
        """Deliver one SMS. Raises `SmsSendError` if the vendor rejects it."""


class SmsSendError(RuntimeError):
    pass


class FakeSMSProvider(SMSProvider):
    """Deterministic in-memory SMS. Used by tests and local dev.

    Records every send on `self.sent` so tests can assert on delivery without a
    vendor account, and never touches the network.
    """

    name = "fake"

    def __init__(self, *, log_body: bool = False) -> None:
        self.sent: list[SmsMessage] = []
        # Off by default: OTP bodies are credentials and must not hit the log
        # aggregator. Local dev turns it on via OTP_DEBUG_ECHO.
        self._log_body = log_body

    async def send(self, message: SmsMessage) -> SmsResult:
        self.sent.append(message)
        if self._log_body:
            logger.info("fake-sms to=%s body=%s", message.to, message.body)
        else:
            logger.info("fake-sms to=%s len=%d", message.to, len(message.body))
        return SmsResult(
            provider=self.name,
            message_id=f"fake-{len(self.sent)}",
            accepted=True,
        )

    @property
    def last(self) -> SmsMessage | None:
        return self.sent[-1] if self.sent else None
