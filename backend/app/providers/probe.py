"""One real round-trip against a vendor, for the console's "test" button (S-GL.1).

Doc 12 §7 asks for a test that "does one real round-trip and reports the vendor's
own error". That last clause is the whole feature. An admin who has just typed a
Meta token into a form needs to know whether *Meta* accepted it, and the useful
answer is Meta's: "Error validating access token: Session has expired". A probe
that flattens that into "connection failed" has told them nothing they can act
on, and they will spend the afternoon re-typing a token that was correct.

**The probe must not do anything to a patient.** So each one is a read: Meta's
phone-number metadata, Exotel's account. Neither sends a message, dials a number,
or costs anything — checking a credential must never be the thing that puts a
call on someone's phone.

The fake providers answer honestly rather than reporting success: on a local
stack there is no vendor to reach, and a green tick against a fake would be the
most misleading possible result on the one screen whose job is telling an
operator what is actually configured.
"""

from __future__ import annotations

import io
import wave

import httpx

from app.config import Settings
from app.providers.audio import AudioClip
from app.providers.llm import LLMRequest
from app.providers.profiles import resolve_profile, snapshot_profile

#: Same ceiling as an ordinary vendor call. A probe that hangs is a console that
#: hangs, and the answer "the vendor did not respond in ten seconds" is itself
#: information.
TIMEOUT_SECONDS = 10.0


class ProbeUnsupported(Exception):
    """No probe exists for this vendor — said plainly, never faked as a pass."""


async def probe(kind: str, vendor: str, settings: Settings) -> str:
    """Reach the vendor once. Returns a human line on success; raises on failure.

    The raised exception's message is shown to the admin verbatim, so it must be
    the vendor's own words wherever the vendor gave us any.
    """
    match f"{kind}:{vendor}":
        case "messaging:meta":
            return await _probe_meta(settings)
        case "telephony:exotel":
            return await _probe_exotel(settings)
    raise ProbeUnsupported(
        f"no connectivity test for {kind}:{vendor} — this vendor cannot be verified from here"
    )


async def probe_voice_component(component: str, vendor: str, settings: Settings) -> str:
    """Exercise one exact cloud profile component with non-patient fixture data."""
    if vendor not in {"openai", "sarvam"} or component not in {"stt", "llm", "tts"}:
        raise ProbeUnsupported(f"no voice-component test for {vendor}:{component}")
    profile = snapshot_profile(f"{vendor}_cloud", settings)
    trio = resolve_profile(profile, settings)
    provider = getattr(trio, component)[0]

    if component == "stt":
        transcript = await provider.transcribe(
            _silent_wav(),
            "en",  # type: ignore[union-attr]
        )
        detail = f"silence accepted ({len(transcript.text)} transcript characters)"
    elif component == "llm":
        result = await provider.complete(  # type: ignore[union-attr]
            LLMRequest(prompt="Reply with the single word OK.", max_tokens=8)
        )
        detail = f"completion accepted ({len(result.text)} response characters)"
    else:
        speech = await provider.synthesize("Voice profile test.", "en")  # type: ignore[union-attr]
        detail = f"speech accepted ({len(speech.audio.data)} audio bytes)"
    return f"{vendor} {component} model {provider.model}: {detail}"


def _silent_wav() -> AudioClip:
    """A valid 250 ms PCM fixture; no patient audio ever enters a credential test."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * 4_000)
    return AudioClip(
        data=buf.getvalue(),
        mime="audio/wav",
        sample_rate=16_000,
        duration_seconds=None,
    )


async def _probe_meta(settings: Settings) -> str:
    """Read the configured phone number's own metadata (Graph API).

    A GET on the phone number id: it proves the token is valid, that it has
    access to *this* number, and that the number is the one the operator meant —
    the reply carries the display number, which is worth showing back.
    """
    url = f"https://graph.facebook.com/v21.0/{settings.meta_phone_number_id}"
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
        response = await http.get(
            url,
            params={"fields": "display_phone_number,verified_name,quality_rating"},
            headers={"Authorization": f"Bearer {settings.meta_whatsapp_token}"},
        )
    if response.status_code >= 400:
        raise RuntimeError(_meta_error(response))
    body = response.json()
    number = body.get("display_phone_number") or settings.meta_phone_number_id
    name = body.get("verified_name") or "unverified"
    return f"Reached WhatsApp number {number} ({name})."


def _meta_error(response: httpx.Response) -> str:
    """Meta's own message, dug out of its envelope."""
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        error = {}
    message = error.get("message") or response.text.strip() or f"HTTP {response.status_code}"
    detail = error.get("error_user_msg")
    return f"Meta: {message}" + (f" — {detail}" if detail else "")


async def _probe_exotel(settings: Settings) -> str:
    """Read the Exotel account. Dials nothing, sends nothing, costs nothing."""
    url = f"https://{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}.json"
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
        response = await http.get(url, auth=(settings.exotel_api_key, settings.exotel_token))
    if response.status_code >= 400:
        raise RuntimeError(_exotel_error(response))
    caller = settings.exotel_caller_id or "no caller id set"
    return f"Reached Exotel account {settings.exotel_sid} (caller id: {caller})."


def _exotel_error(response: httpx.Response) -> str:
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    message = (
        (body.get("RestException") or {}).get("Message")
        or body.get("message")
        or response.text.strip()
        or f"HTTP {response.status_code}"
    )
    if response.status_code == 401:
        message = f"{message} (check the API key and token, not the SID)"
    return f"Exotel: {message}"
