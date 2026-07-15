"""TelephonyProvider — outbound/inbound PSTN (doc 02 §2: Exotel).

Carries the D-1 outbound intake campaign (S15), the inbound AI receptionist
(S15), and the human-handoff transfer. The *conversational* half — Exotel's
Voicebot applet streaming audio over a websocket — is `voice-gw`'s job in S14;
this interface is the control plane: place a call, know what happened to it, hang
up.

## Metering a call you are not on

Exotel bills per minute, and the duration is only known when the call ends —
which we learn from a status callback, in a different request, minutes later. So
metering here is two-part:

- `place_call` meters nothing (no minutes have happened yet), and
- `record_call_completed` meters the duration when the callback arrives (S15
  wires it to the webhook).

Long calls also meter as they go from `voice-gw` (S14), per doc 02 §5's
per-minute audio metering — a 9-minute intake must not be invisible to the
cost-guard for 9 minutes.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

import httpx

from app.models.enums import UsagePurpose
from app.providers.base import Provider, ProviderBadRequest, ProviderUnavailable
from app.providers.metering import MeterCall, UsageDelta

logger = logging.getLogger(__name__)


class CallState(StrEnum):
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    NO_ANSWER = "no-answer"

    @property
    def is_final(self) -> bool:
        return self in {
            CallState.COMPLETED,
            CallState.FAILED,
            CallState.BUSY,
            CallState.NO_ANSWER,
        }


@dataclass(frozen=True, slots=True)
class CallRequest:
    to: str
    #: The Exotel applet/flow that answers when the patient picks up. For a
    #: conversational intake this is the Voicebot applet pointing at voice-gw.
    applet_url: str
    caller_id: str | None = None
    status_callback: str | None = None
    #: Attribution, echoed back on the callback so a cost lands on the right
    #: intake — Exotel gives us a custom field, and this is what it is for.
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class CallHandle:
    provider: str
    call_sid: str
    state: CallState
    duration_seconds: Decimal | None = None


class TelephonyProvider(Provider):
    kind: ClassVar[str] = "telephony"

    async def place_call(
        self, request: CallRequest, *, purpose: UsagePurpose = UsagePurpose.INTAKE_TURN
    ) -> CallHandle:
        return await self._invoke(
            purpose, lambda call: self._place_call(request, call), model=self.name
        )

    @abstractmethod
    async def _place_call(self, request: CallRequest, call: MeterCall) -> CallHandle:
        """Dial. Meters nothing — no minutes have been billed yet."""

    @abstractmethod
    async def get_call(self, call_sid: str) -> CallHandle:
        """Poll one call's state. The callback is the primary path; this is for
        reconciliation when a callback is lost."""

    def record_call_completed(self, handle: CallHandle) -> None:
        """Meter a finished call's minutes.

        Called from S15's status-callback webhook, not from a provider method:
        the cost arrives as an inbound HTTP request minutes after the call, and
        pretending otherwise would mean either blocking on a call to finish or
        never billing it.
        """
        if handle.duration_seconds is None or handle.duration_seconds <= 0:
            return
        self._meter_stream(
            UsagePurpose.INTAKE_TURN,
            UsageDelta(audio_seconds=handle.duration_seconds),
            model=self.name,
        )


class FakeTelephonyProvider(TelephonyProvider):
    """Deterministic telephony. Records dials; completes calls on demand.

    S15's campaign tests drive this: place calls, then `complete()` the ones a
    patient "answered" and leave the rest to the retry ladder.
    """

    name: ClassVar[str] = "fake-telephony"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.placed: list[CallRequest] = []
        self.calls: dict[str, CallHandle] = {}
        self.fail_with: Exception | None = None

    async def _place_call(self, request: CallRequest, call: MeterCall) -> CallHandle:
        if self.fail_with is not None:
            raise self.fail_with
        self.placed.append(request)
        sid = f"fake-call-{len(self.placed)}"
        handle = CallHandle(provider=self.name, call_sid=sid, state=CallState.QUEUED)
        self.calls[sid] = handle
        return handle

    async def get_call(self, call_sid: str) -> CallHandle:
        try:
            return self.calls[call_sid]
        except KeyError:
            raise ProviderBadRequest(f"no such call {call_sid!r}") from None

    def complete(self, call_sid: str, *, seconds: int = 120) -> CallHandle:
        """Simulate the status callback for a call that connected and ended."""
        handle = CallHandle(
            provider=self.name,
            call_sid=call_sid,
            state=CallState.COMPLETED,
            duration_seconds=Decimal(seconds),
        )
        self.calls[call_sid] = handle
        self.record_call_completed(handle)
        return handle

    @property
    def last(self) -> CallRequest | None:
        return self.placed[-1] if self.placed else None


class ExotelTelephonyProvider(TelephonyProvider):
    """Exotel — `POST /v1/Accounts/<sid>/Calls/connect.json`.

    Wire notes: form-encoded, HTTP basic auth with (api_key, api_token), and the
    response nests everything under `Call`. `Url` is the applet to run when the
    callee answers; for conversational intake that applet is the Voicebot one
    that opens a websocket to `voice-gw` (S14).
    """

    name: ClassVar[str] = "exotel"

    def __init__(
        self,
        *,
        sid: str,
        api_key: str,
        api_token: str,
        caller_id: str,
        subdomain: str = "api.exotel.com",
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(sid and api_key and api_token), **kwargs)
        self._sid = sid
        self._caller_id = caller_id
        self._client = client or httpx.AsyncClient(
            base_url=f"https://{subdomain}/v1/Accounts/{sid}",
            auth=(api_key, api_token),
            timeout=self.timeout_seconds,
        )

    @staticmethod
    def _to_handle(payload: dict) -> CallHandle:
        raw_state = str(payload.get("Status", "queued")).lower()
        try:
            state = CallState(raw_state)
        except ValueError:
            # An unknown state from the vendor is not a crash: log it, treat it
            # as in-progress, and let the callback settle it.
            logger.warning("unknown exotel call state %r", raw_state)
            state = CallState.IN_PROGRESS
        duration = payload.get("Duration")
        return CallHandle(
            provider=ExotelTelephonyProvider.name,
            call_sid=str(payload.get("Sid", "")),
            state=state,
            duration_seconds=Decimal(str(duration)) if duration not in (None, "") else None,
        )

    async def _place_call(self, request: CallRequest, call: MeterCall) -> CallHandle:
        form = {
            "From": request.to,  # Exotel dials `From` first, then connects `Url`
            "CallerId": request.caller_id or self._caller_id,
            "Url": request.applet_url,
        }
        if request.status_callback:
            form["StatusCallback"] = request.status_callback
        if request.reference:
            form["CustomField"] = request.reference

        try:
            response = await self._client.post("/Calls/connect.json", data=form)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"exotel transport error: {exc}") from exc

        if response.status_code in (400, 401, 403):
            raise ProviderBadRequest(f"exotel rejected the call: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(f"exotel http {response.status_code}: {response.text[:200]}")

        return self._to_handle(response.json().get("Call", {}))

    async def get_call(self, call_sid: str) -> CallHandle:
        try:
            response = await self._client.get(f"/Calls/{call_sid}.json")
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"exotel transport error: {exc}") from exc
        if response.status_code == 404:
            raise ProviderBadRequest(f"no such call {call_sid!r}")
        if response.status_code >= 300:
            raise ProviderUnavailable(f"exotel http {response.status_code}: {response.text[:200]}")
        return self._to_handle(response.json().get("Call", {}))
