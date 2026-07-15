"""SMSProvider — transactional SMS (doc 02 §9).

Carries OTP login today; Rx delivery (S11) and the check-in fallback rung (S17)
land on the same interface.

## Two vendors, on purpose

MSG91 and Exotel SMS are both implemented and both config-switchable
(`SMS_PROVIDER=msg91|exotel|fake`). The pilot has not committed to either, and
this is a cheap place to stay uncommitted: whichever account clears DLT approval
first wins, and the other stays as the failover for the day the winner has an
outage. Nothing above this module changes either way.

## What DLT means for this interface

India's TRAI DLT regime does not allow arbitrary transactional text. Every
message is a *registered template* with variables the vendor substitutes. So
`SmsMessage` carries both:

- `variables` — what MSG91 actually transmits (template id + variables), and
- `body` — the rendered text, for the fake, for logs, and for Exotel, whose API
  takes a rendered body and matches it against the registered template.

Rendering happens above this layer, once, so the two vendors cannot drift on
what a patient actually reads.

## Verification status

Both impls are written against the vendors' documented HTTP APIs and covered by
tests through a mocked transport. **No live account has accepted a message yet**,
and template/sender ids are per-account. Registered in STATE.md → Stubs & fakes:
the first live send needs a human watching a real handset.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

import httpx

from app.models.enums import UsagePurpose
from app.providers.base import Provider, ProviderBadRequest, ProviderUnavailable
from app.providers.metering import MeterCall, UsageDelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmsMessage:
    to: str
    body: str
    # Registered DLT template — required by both vendors for transactional SMS.
    template_key: str | None = None
    # Template variables, named as the registered DLT template declares them.
    variables: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SmsResult:
    provider: str
    message_id: str
    accepted: bool
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SmsSendError(ProviderUnavailable):
    """The S2 name for "this send failed" (the OTP flow catches it), now an alias
    of the provider layer's fall-back-or-downgrade signal."""


def normalise_msisdn(phone: str, *, default_cc: str = "91") -> str:
    """`+91 98765 43210` → `919876543210`, the format both vendors want.

    Rejects rather than guesses when a number is not plausibly dialable: silently
    sending an OTP to a mangled number locks a doctor out of the console with no
    visible error, which is a worse morning than a 400.
    """
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not phone.strip().startswith("+"):
        if len(digits) == 10:
            digits = default_cc + digits
        elif len(digits) == 11 and digits.startswith("0"):
            digits = default_cc + digits[1:]
    if not (10 <= len(digits) <= 15):  # E.164 caps at 15
        raise ProviderBadRequest(f"implausible phone number: {phone!r}")
    return digits


class SMSProvider(Provider):
    """Send one transactional SMS.

    Impls implement `_send`; the public `send` is metered, retried and
    health-tracked by `Provider._invoke` — that is why `_send` is the private one
    (see base.py: metering you cannot forget).
    """

    kind: ClassVar[str] = "sms"

    async def send(
        self, message: SmsMessage, *, purpose: UsagePurpose = UsagePurpose.OTHER
    ) -> SmsResult:
        return await self._invoke(purpose, lambda call: self._send(message, call), model=self.name)

    @abstractmethod
    async def _send(self, message: SmsMessage, call: MeterCall) -> SmsResult:
        """Deliver one SMS. Report usage on `call`; raise `ProviderUnavailable`
        for vendor/transport faults, `ProviderBadRequest` for rejected input."""


class FakeSMSProvider(SMSProvider):
    """Deterministic in-memory SMS for tests and local dev; never touches the network."""

    name: ClassVar[str] = "fake-sms"

    def __init__(self, *, log_body: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sent: list[SmsMessage] = []
        # Off by default: OTP bodies are credentials and must not reach the log
        # aggregator. Local dev turns it on via OTP_DEBUG_ECHO.
        self._log_body = log_body

    async def _send(self, message: SmsMessage, call: MeterCall) -> SmsResult:
        normalise_msisdn(message.to)  # reject the same inputs the real ones reject
        self.sent.append(message)
        if self._log_body:
            logger.info("fake-sms to=%s body=%s", message.to, message.body)
        else:
            logger.info("fake-sms to=%s len=%d", message.to, len(message.body))
        call.usage = UsageDelta(messages=1)
        return SmsResult(provider=self.name, message_id=f"fake-{len(self.sent)}", accepted=True)

    @property
    def last(self) -> SmsMessage | None:
        return self.sent[-1] if self.sent else None


class Msg91SMSProvider(SMSProvider):
    """MSG91 Flow API v5 — `POST /api/v5/flow/`.

    Flow transmits `template_id` + variables and renders from the template on
    MSG91's side; our rendered body is not sent. That is why a missing template
    id is a hard error rather than a fall back to raw text — raw text is what the
    DLT filter drops silently.
    """

    name: ClassVar[str] = "msg91"

    BASE_URL: ClassVar[str] = "https://control.msg91.com/api/v5"

    def __init__(
        self,
        *,
        auth_key: str,
        sender_id: str,
        template_ids: Mapping[str, str],
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(auth_key), **kwargs)
        self._auth_key = auth_key
        self._sender_id = sender_id
        self._template_ids = dict(template_ids)
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=self.timeout_seconds
        )

    def _template_id(self, key: str | None) -> str:
        if not key:
            raise ProviderBadRequest("msg91 requires a template_key (DLT)")
        try:
            return self._template_ids[key]
        except KeyError:
            raise ProviderBadRequest(
                f"no MSG91 template id configured for {key!r}; set MSG91_TEMPLATE_IDS"
            ) from None

    async def _send(self, message: SmsMessage, call: MeterCall) -> SmsResult:
        payload = {
            "template_id": self._template_id(message.template_key),
            "short_url": "0",
            "sender": self._sender_id,
            "recipients": [{"mobiles": normalise_msisdn(message.to), **dict(message.variables)}],
        }
        try:
            response = await self._client.post(
                "/flow/",
                json=payload,
                headers={"authkey": self._auth_key, "accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"msg91 transport error: {exc}") from exc

        if response.status_code == 400:
            # MSG91 uses 400 for template/variable mistakes — our bug, not their outage.
            raise ProviderBadRequest(f"msg91 rejected the request: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(f"msg91 http {response.status_code}: {response.text[:200]}")

        body = response.json()
        # MSG91 returns HTTP 200 with {"type": "error"} for some failures, so the
        # status code alone is not proof of acceptance.
        if str(body.get("type", "")).lower() == "error":
            raise ProviderUnavailable(f"msg91 error: {body.get('message')}")

        call.usage = UsageDelta(messages=1)
        return SmsResult(
            provider=self.name,
            message_id=str(body.get("request_id") or body.get("message") or ""),
            accepted=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class ExotelSMSProvider(SMSProvider):
    """Exotel SMS — `POST /v1/Accounts/<sid>/Sms/send.json`.

    Takes the rendered body and matches it against the template registered under
    `DltTemplateId` — the other half of `SmsMessage` from MSG91's, same interface.

    Worth knowing if this becomes the pick: Exotel is already the telephony
    vendor (doc 02 §2), so choosing it here means one vendor relationship and one
    set of credentials — and one outage that takes SMS *and* the phone intake
    channel down together. MSG91 keeps those failure domains apart. That tradeoff
    is the human's call, which is why both are here.
    """

    name: ClassVar[str] = "exotel"

    def __init__(
        self,
        *,
        sid: str,
        api_key: str,
        api_token: str,
        sender_id: str,
        subdomain: str = "api.exotel.com",
        dlt_entity_id: str = "",
        dlt_template_ids: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(sid and api_key and api_token), **kwargs)
        self._sid = sid
        self._sender_id = sender_id
        self._dlt_entity_id = dlt_entity_id
        self._dlt_template_ids = dict(dlt_template_ids or {})
        self._client = client or httpx.AsyncClient(
            base_url=f"https://{subdomain}/v1/Accounts/{sid}",
            auth=(api_key, api_token),
            timeout=self.timeout_seconds,
        )

    async def _send(self, message: SmsMessage, call: MeterCall) -> SmsResult:
        form = {
            "From": self._sender_id,
            "To": normalise_msisdn(message.to),
            "Body": message.body,
            "DltEntityId": self._dlt_entity_id,
        }
        if message.template_key:
            template_id = self._dlt_template_ids.get(message.template_key)
            if not template_id:
                raise ProviderBadRequest(
                    f"no Exotel DLT template id for {message.template_key!r}; "
                    "set EXOTEL_DLT_TEMPLATE_IDS"
                )
            form["DltTemplateId"] = template_id

        try:
            response = await self._client.post("/Sms/send.json", data=form)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"exotel transport error: {exc}") from exc

        if response.status_code in (400, 401, 403):
            raise ProviderBadRequest(f"exotel rejected the request: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(f"exotel http {response.status_code}: {response.text[:200]}")

        sms = response.json().get("SMSMessage", {})
        call.usage = UsageDelta(messages=1)
        return SmsResult(
            provider=self.name,
            message_id=str(sms.get("Sid", "")),
            accepted=str(sms.get("Status", "")).lower()
            in {"queued", "sending", "sent", "submitted"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
