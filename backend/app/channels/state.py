"""Is this channel open, and if not, what does the patient hear?

`enabled` (the operator's switch, in the published document) and `ready` (whether
the vendor the channel needs is actually configured) are computed separately and
reported separately — see the package docstring for why. `open` is both.

**Readiness is computed, never stored.** A console can turn a channel off; it
cannot assert that Meta is provisioned when it is not. That inversion is the
point of doc 12 §7's note — "treat disabled as the safe default for any channel
with no tested credentials, going live should not require remembering to switch
things off". A hospital that forgets to close WhatsApp before go-live still gets a
closed WhatsApp, because no credentials means not ready, and not ready means
closed regardless of the switch.

The kiosk and the app need no vendor to run an intake (local voice on the box, or
the browser's own speech, down to the zero-AI floor), so they are ready by
construction. That is a real asymmetry, not an oversight: it is why doc 12 §8's
go-live is kiosk-first.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.models.enums import Channel, Lang
from app.tiers import SWITCHABLE, TierConfig

#: What a patient is told when a channel is shut, in each pilot language.
#:
#: Deliberately about the desk and not about us: "the service is unavailable"
#: tells a frightened person nothing, and every channel this can fire on has a
#: human alternative twenty metres away. Every line therefore names where to go
#: instead — a closed channel is a redirection, not an error.
#:
#: All four languages because this is patient-facing text on a channel a patient
#: chose in her own language; `app.lang_qa` checks the set the way it checks the
#: trees and the templates. mr/te are model-drafted and join the S21 native
#: review with everything else.
CLOSED_MESSAGE: dict[Channel, dict[Lang, str]] = {
    Channel.KIOSK: {
        Lang.EN: "The kiosk is not taking registrations right now. Please see the front desk.",
        Lang.HI: "यह कियोस्क अभी पंजीकरण नहीं ले रहा है। कृपया रिसेप्शन काउंटर पर संपर्क करें।",
        Lang.MR: "हे कियोस्क सध्या नोंदणी घेत नाही. कृपया रिसेप्शन काउंटरवर संपर्क साधा.",
        Lang.TE: "ఈ కియోస్క్ ప్రస్తుతం నమోదు తీసుకోవడం లేదు. దయచేసి రిసెప్షన్ కౌంటర్‌ను సంప్రదించండి.",
    },
    Channel.PHONE: {
        Lang.EN: "Phone registration is not open yet. Please visit the OPD desk, or use the kiosk in the waiting area.",  # noqa: E501
        Lang.HI: "फ़ोन से पंजीकरण अभी शुरू नहीं हुआ है। कृपया ओपीडी काउंटर पर आएं, या प्रतीक्षा क्षेत्र में लगे कियोस्क का उपयोग करें।",  # noqa: E501
        Lang.MR: "फोनवरून नोंदणी अद्याप सुरू झालेली नाही. कृपया ओपीडी काउंटरवर या, किंवा प्रतीक्षा कक्षातील कियोस्क वापरा.",  # noqa: E501
        Lang.TE: "ఫోన్ ద్వారా నమోదు ఇంకా ప్రారంభం కాలేదు. దయచేసి ఓపీడీ కౌంటర్‌కు రండి, లేదా వెయిటింగ్ ఏరియాలోని కియోస్క్ ఉపయోగించండి.",  # noqa: E501
    },
    Channel.WHATSAPP: {
        Lang.EN: "This number is not answering registrations yet. Please visit the OPD desk — the staff there will register you.",  # noqa: E501
        Lang.HI: "इस नंबर पर पंजीकरण अभी शुरू नहीं हुआ है। कृपया ओपीडी काउंटर पर आएं — वहां का स्टाफ़ आपका पंजीकरण कर देगा।",  # noqa: E501
        Lang.MR: "या नंबरवर नोंदणी अद्याप सुरू झालेली नाही. कृपया ओपीडी काउंटरवर या — तिथले कर्मचारी तुमची नोंदणी करतील.",  # noqa: E501
        Lang.TE: "ఈ నంబర్‌లో నమోదు ఇంకా ప్రారంభం కాలేదు. దయచేసి ఓపీడీ కౌంటర్‌కు రండి — అక్కడి సిబ్బంది మీ నమోదు పూర్తి చేస్తారు.",  # noqa: E501
    },
    Channel.APP: {
        Lang.EN: "Registration from the app is not open yet. Please use the kiosk in the OPD or see the front desk.",  # noqa: E501
        Lang.HI: "ऐप से पंजीकरण अभी शुरू नहीं हुआ है। कृपया ओपीडी में लगे कियोस्क का उपयोग करें या रिसेप्शन काउंटर पर संपर्क करें।",  # noqa: E501
        Lang.MR: "अ‍ॅपवरून नोंदणी अद्याप सुरू झालेली नाही. कृपया ओपीडीमधील कियोस्क वापरा किंवा रिसेप्शन काउंटरवर संपर्क साधा.",  # noqa: E501
        Lang.TE: "యాప్ ద్వారా నమోదు ఇంకా ప్రారంభం కాలేదు. దయచేసి ఓపీడీలోని కియోస్క్ ఉపయోగించండి లేదా రిసెప్షన్ కౌంటర్‌ను సంప్రదించండి.",  # noqa: E501
    },
}

_DEFAULT_CLOSED = "This is not open yet. Please see the OPD desk."


def closed_message(channel: Channel, lang: Lang = Lang.EN) -> str:
    """The patient-facing line for a shut channel, in her language.

    Falls back to English rather than to nothing: a missing translation should
    read as a sentence she may not understand, next to a member of staff who
    does, rather than as an empty message.
    """
    lines = CLOSED_MESSAGE.get(channel)
    if not lines:
        return _DEFAULT_CLOSED
    return lines.get(lang) or lines.get(Lang.EN) or _DEFAULT_CLOSED


class ChannelClosed(Exception):
    """A patient reached a channel that is not open. Rendered as a civil 503.

    An exception rather than a returned flag so a route cannot forget to check
    the result and carry on into the intake anyway.
    """

    def __init__(self, state: ChannelState, lang: Lang = Lang.EN) -> None:
        super().__init__(f"{state.channel.value} is closed: {state.reason}")
        self.state = state
        self.lang = lang

    @property
    def message(self) -> str:
        return self.state.message(self.lang)


@dataclass(frozen=True, slots=True)
class ChannelState:
    """Everything the console and the gate need to know about one channel."""

    channel: Channel
    enabled: bool
    ready: bool
    #: Why it is not open, in the console's words ("switched off", "no Meta
    #: credentials"). Empty when it is open.
    reason: str
    ladder: tuple[str, ...]
    max_concurrent: int
    #: A caveat about an *open* channel. One thing says it today, and it is the
    #: one that would otherwise mislead an operator on the pilot box: the box runs
    #: `ENV=local` (it must, or `assert_production_safe` would refuse to boot with
    #: no Meta or Exotel account), so a `fake` provider counts as ready — and a
    #: row reading "Open · configured" for a WhatsApp that does not exist is
    #: exactly the false green this whole tab exists to prevent.
    note: str = ""

    @property
    def is_open(self) -> bool:
        return self.enabled and self.ready

    def message(self, lang: Lang = Lang.EN) -> str:
        """What the patient hears. Never the reason — "no Meta credentials" is
        an answer for the operator, not for someone trying to register."""
        return closed_message(self.channel, lang)


#: What an open channel running a fake provider is told to say about itself. Not
#: a refusal — the dev stack and the demo depend on the fake working — but it must
#: never read as a provisioned vendor.
FAKE_NOTE = "running the fake provider — no real vendor is connected"


def readiness(channel: Channel, settings: Settings | None = None) -> tuple[bool, str, str]:
    """Is the vendor this channel needs configured? `(ready, reason_if_not, note)`.

    A `fake` provider counts as configured **only on a local or test box**, where
    it is the normal state and closing every channel would make the dev stack and
    the demo useless. Outside local it does not, and it cannot:
    `assert_production_safe` refuses to boot a non-local env with a fake provider
    at all.

    It counts, but it says so. The pilot box runs `ENV=local` — it has to, with no
    Meta or Exotel account to satisfy `assert_production_safe` — so without the
    note an operator would read "WhatsApp · Open · configured" off a box where
    messaging goes nowhere. That is the precise false green this tab exists to
    prevent, so the caveat travels with the answer rather than being left for
    somebody to infer.
    """
    settings = settings or get_settings()
    match channel:
        case Channel.KIOSK | Channel.APP:
            # No vendor: local voice on the box, the browser's own speech, or the
            # zero-AI walker. There is nothing that can be unprovisioned.
            return True, "", ""
        case Channel.WHATSAPP:
            vendor = settings.messaging_provider
            if vendor == "fake":
                return _fake_ready(settings, "messaging")
            if vendor == "meta" and not (
                settings.meta_whatsapp_token and settings.meta_phone_number_id
            ):
                return False, "no Meta credentials — set them in Channels → WhatsApp", ""
            return True, "", ""
        case Channel.PHONE:
            vendor = settings.telephony_provider
            if vendor == "fake":
                return _fake_ready(settings, "telephony")
            if vendor == "exotel" and not (
                settings.exotel_sid and settings.exotel_api_key and settings.exotel_token
            ):
                return False, "no Exotel credentials — set them in Channels → Phone", ""
            return True, "", ""
    return True, "", ""


def _fake_ready(settings: Settings, kind: str) -> tuple[bool, str, str]:
    if settings.is_local:
        return True, "", FAKE_NOTE
    return False, f"the {kind} provider is still 'fake' — no vendor is configured", ""


def channel_state(
    config: TierConfig, channel: Channel, settings: Settings | None = None
) -> ChannelState:
    policy = config.policy_for(channel)
    ready, why, note = readiness(channel, settings)
    reason = ""
    if not policy.enabled:
        reason = "switched off in the admin console"
    elif not ready:
        reason = why
    return ChannelState(
        channel=channel,
        enabled=policy.enabled,
        ready=ready,
        reason=reason,
        ladder=policy.ladder,
        max_concurrent=policy.max_concurrent,
        note=note,
    )


def channel_states(config: TierConfig, settings: Settings | None = None) -> list[ChannelState]:
    """Every switchable channel's state — what the console's Channels tab renders."""
    return [channel_state(config, channel, settings) for channel in SWITCHABLE]


def require_open(
    config: TierConfig,
    channel: Channel,
    settings: Settings | None = None,
    *,
    lang: Lang = Lang.EN,
) -> None:
    """The gate. Raises `ChannelClosed` unless the channel is switched on *and* ready.

    `lang` is the patient's, when the entry point knows it — the kiosk and the app
    both send one. A caller that does not know yet (a WhatsApp thread whose first
    message is a "hello") gets English, which is what the bot's own greeting does.
    """
    state = channel_state(config, channel, settings)
    if not state.is_open:
        raise ChannelClosed(state, lang)
