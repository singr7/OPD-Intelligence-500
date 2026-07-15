"""MessagingProvider — WhatsApp (doc 02 §2: Meta Cloud API, no BSP lock-in).

Carries the WhatsApp intake bot (S12), Rx delivery (S11), and the top rung of the
check-in delivery ladder (S17). Voice notes are first-class here, not an
afterthought: doc 02 §2 picks Meta partly because voice notes "cover the
'WhatsApp calling' use case pragmatically" for patients who cannot read.

## Billing is per conversation, and this layer does not know that yet

Meta bills per 24-hour *conversation window* by category, not per message. This
impl meters `messages=1` per send, which **over-counts** — five messages in one
window is one billable conversation, not five. It is wired this way on purpose:
session windows are S12's build (doc 06), and conversation-level attribution
belongs where the window state lives. Until then the dashboard reads WhatsApp
cost as a ceiling. Registered in HANDOFF → Backlog; it must be closed before the
S18 invoice reconciliation can be honest.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from app.models.enums import UsagePurpose
from app.providers.audio import AudioClip
from app.providers.base import Provider, ProviderBadRequest, ProviderUnavailable
from app.providers.metering import MeterCall, UsageDelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Button:
    """An interactive reply button. Meta caps the title at 20 characters, and
    truncation is silent — so `title` is validated here, not discovered in the
    field by a patient staring at a clipped Hindi word."""

    id: str
    title: str

    def __post_init__(self) -> None:
        if len(self.title) > 20:
            raise ProviderBadRequest(f"button title over 20 chars: {self.title!r}")


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    to: str
    text: str = ""
    buttons: Sequence[Button] = ()
    audio: AudioClip | None = None
    #: Set for out-of-window sends — Meta only accepts a registered template
    #: after the 24h window closes. The template registry lands in S12.
    template_name: str | None = None
    template_lang: str | None = None
    template_variables: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class MessageResult:
    provider: str
    message_id: str
    accepted: bool


class MessagingProvider(Provider):
    kind: ClassVar[str] = "messaging"

    async def send(
        self, message: OutboundMessage, *, purpose: UsagePurpose = UsagePurpose.OTHER
    ) -> MessageResult:
        return await self._invoke(purpose, lambda call: self._send(message, call), model=self.name)

    @abstractmethod
    async def _send(self, message: OutboundMessage, call: MeterCall) -> MessageResult:
        """Deliver one message; report `messages` on `call`."""

    @abstractmethod
    async def download_media(self, media_id: str) -> AudioClip:
        """Fetch an inbound voice note. S12's webhook gets an id, not the bytes."""


class FakeMessagingProvider(MessagingProvider):
    """Deterministic WhatsApp. Records sends; never touches the network."""

    name: ClassVar[str] = "fake-whatsapp"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sent: list[OutboundMessage] = []
        self.media: dict[str, AudioClip] = {}
        self.fail_with: Exception | None = None

    async def _send(self, message: OutboundMessage, call: MeterCall) -> MessageResult:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(message)
        call.usage = UsageDelta(messages=1)
        return MessageResult(
            provider=self.name, message_id=f"fake-wa-{len(self.sent)}", accepted=True
        )

    async def download_media(self, media_id: str) -> AudioClip:
        try:
            return self.media[media_id]
        except KeyError:
            raise ProviderBadRequest(f"fake messaging has no media {media_id!r}") from None

    @property
    def last(self) -> OutboundMessage | None:
        return self.sent[-1] if self.sent else None


class MetaWhatsAppProvider(MessagingProvider):
    """Meta WhatsApp Cloud API — `POST /<phone_number_id>/messages`.

    Wire notes for the next graph version bump: message type is a top-level
    discriminator (`text` | `interactive` | `audio` | `template`), each with its
    own sibling object; errors come back as HTTP 400 with a nested
    `error.message` that is the only useful part.
    """

    name: ClassVar[str] = "meta"

    GRAPH_VERSION: ClassVar[str] = "v21.0"
    BASE_URL: ClassVar[str] = "https://graph.facebook.com"

    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(access_token and phone_number_id), **kwargs)
        self._token = access_token
        self._phone_number_id = phone_number_id
        self._client = client or httpx.AsyncClient(
            base_url=f"{self.BASE_URL}/{self.GRAPH_VERSION}", timeout=self.timeout_seconds
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _payload(self, message: OutboundMessage) -> dict[str, Any]:
        base: dict[str, Any] = {"messaging_product": "whatsapp", "to": message.to}

        if message.template_name:
            return base | {
                "type": "template",
                "template": {
                    "name": message.template_name,
                    "language": {"code": message.template_lang or "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": v} for v in message.template_variables
                            ],
                        }
                    ]
                    if message.template_variables
                    else [],
                },
            }

        if message.buttons:
            return base | {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": message.text},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": b.id, "title": b.title}}
                            for b in message.buttons
                        ]
                    },
                },
            }

        if message.audio is not None:
            # Audio needs a prior /media upload; S12 owns that flow, and sending
            # a voice note without it should fail loudly rather than send silence.
            raise ProviderBadRequest(
                "audio sends need a media upload first (S12); use text for now"
            )

        return base | {"type": "text", "text": {"body": message.text}}

    async def _send(self, message: OutboundMessage, call: MeterCall) -> MessageResult:
        payload = self._payload(message)
        try:
            response = await self._client.post(
                f"/{self._phone_number_id}/messages", json=payload, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"meta transport error: {exc}") from exc

        if response.status_code == 400:
            detail = (response.json().get("error") or {}).get("message", response.text[:200])
            raise ProviderBadRequest(f"meta rejected the message: {detail}")
        if response.status_code >= 300:
            raise ProviderUnavailable(f"meta http {response.status_code}: {response.text[:200]}")

        body = response.json()
        call.usage = UsageDelta(messages=1)  # over-counts; see module docstring
        messages = body.get("messages") or [{}]
        return MessageResult(
            provider=self.name, message_id=str(messages[0].get("id", "")), accepted=True
        )

    async def download_media(self, media_id: str) -> AudioClip:
        """Two hops: id → signed URL → bytes. Meta's URLs are short-lived, so
        this cannot be split across a queue boundary — fetch now or lose it."""
        try:
            lookup = await self._client.get(f"/{media_id}", headers=self._headers())
            if lookup.status_code >= 300:
                raise ProviderUnavailable(f"meta media lookup {lookup.status_code}")
            url = lookup.json().get("url")
            if not url:
                raise ProviderUnavailable(f"meta media {media_id} has no url")

            blob = await self._client.get(url, headers=self._headers())
            if blob.status_code >= 300:
                raise ProviderUnavailable(f"meta media fetch {blob.status_code}")
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"meta media transport error: {exc}") from exc

        return AudioClip(data=blob.content, mime=blob.headers.get("content-type", "audio/ogg"))
