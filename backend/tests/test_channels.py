"""The switchboard (S-GL.1, doc 12 §1/§7): what is open, what a closed one says.

Four things are worth proving here, and they are the four ways this can be wrong
in a way nobody notices until a patient is standing there:

1. **The document parses or is refused.** A channel document is the one piece of
   config that can shut the OPD, so every malformed shape fails at parse rather
   than at 9am.
2. **The file is the floor and a publish wins.** Same seam as the trees and the
   protocol banks, including the part that matters most: a *published row that
   does not parse* falls back to the file's open channels, never to closed.
3. **A closed channel is quiet, civil, and non-clinical.** Nothing 500s, nothing
   half-starts, and the kiosk is untouched by whatever the other three are doing.
4. **Readiness cannot be lied about.** A console can close a channel; it cannot
   assert that Meta is provisioned when it is not.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import channel_state, resolve_config
from app.channels.state import CLOSED_MESSAGE, ChannelClosed, readiness, require_open
from app.config import Settings
from app.languages import PILOT_LANGUAGES
from app.models.content import ChannelConfigVersion
from app.models.enums import Channel, ContentStatus, Lang
from app.tiers import SWITCHABLE, TierConfigError, get_tier_config, parse_tier_config

pytestmark = pytest.mark.asyncio


# -- the document -------------------------------------------------------------


def _doc(**channels) -> dict:
    return {
        "channels": {name: spec for name, spec in channels.items()},
        "admission": {"max_oss_sessions": 12},
    }


async def test_the_shipped_file_still_parses_and_opens_every_channel():
    """`config/tiers.yaml` is the floor, so it has to be a valid document — and
    it has to leave the dev stack exactly as it was before S-GL.1."""
    config = get_tier_config()
    for channel in SWITCHABLE:
        assert config.is_enabled(channel), f"{channel.value} closed in the shipped file"
    assert config.ladder_for(Channel.KIOSK) == ("v_oss", "v3")
    assert config.max_oss_sessions == 12


async def test_a_channel_may_be_switched_off_in_the_document():
    config = parse_tier_config(
        _doc(kiosk={"ladder": ["v3"]}, whatsapp={"ladder": ["v2"], "enabled": False})
    )
    assert config.is_enabled(Channel.KIOSK)
    assert not config.is_enabled(Channel.WHATSAPP)


async def test_an_omitted_channel_is_open_on_the_safe_ladder():
    """A document that forgot a channel must not close it. Failing open here is
    deliberate: the failure mode of the alternative is a silently dark OPD."""
    config = parse_tier_config(_doc(kiosk={"ladder": ["v3"]}))
    assert config.is_enabled(Channel.PHONE)
    assert config.ladder_for(Channel.PHONE) == ("v2", "v3")


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"channels": {"telegram": {"ladder": ["v3"]}}}, "unknown channel"),
        ({"channels": {"sms": {"ladder": ["v3"]}}}, "no entry point"),
        ({"channels": {"kiosk": {"ladder": []}}}, "non-empty"),
        ({"channels": {"kiosk": {"ladder": ["v9"]}}}, "unknown tier"),
        ({"channels": {"kiosk": {"ladder": ["v3"], "enabled": "yes"}}}, "true or false"),
        ({"channels": {"kiosk": {"ladder": ["v3"], "max_concurrent": -1}}}, "not be negative"),
        ({"channels": {"kiosk": {"ladder": ["v3"], "max_concurrent": "six"}}}, "an integer"),
        ({"admission": {"max_oss_sessions": -3}}, "non-negative"),
    ],
)
async def test_a_malformed_document_is_refused_at_parse(bad: dict, message: str):
    with pytest.raises(TierConfigError, match=message):
        parse_tier_config(bad)


async def test_a_channel_cannot_reserve_more_seats_than_the_box_has():
    """A share bigger than the cap reads as a cap but is a typo. It would let
    phone take every seat on a box the operator believed was sharing them."""
    with pytest.raises(TierConfigError, match="reserves 20 local voice seats"):
        parse_tier_config(
            {
                "channels": {"phone": {"ladder": ["v_oss"], "max_concurrent": 20}},
                "admission": {"max_oss_sessions": 12},
            }
        )


@pytest.mark.parametrize(
    "mix, message",
    [
        ({"phone": 30, "whatsapp": 60}, "sum to 100"),
        ({"phone": 130, "whatsapp": -30}, "percent 0-100"),
        ({"paper": 100}, "no entry point"),
    ],
)
async def test_a_campaign_mix_that_does_not_add_up_is_refused(mix: dict, message: str):
    """A mix summing to 90 silently drops one patient in ten from tomorrow's
    outreach — the kind of wrong that looks like nothing at all."""
    with pytest.raises(TierConfigError, match=message):
        parse_tier_config({"channels": {}, "campaign": {"mix": mix}})


async def test_the_document_round_trips_through_to_json():
    """The console loads `to_json`, edits it, and posts it back to a draft, where
    the same parser checks it. A lossy round trip would quietly drop a switch."""
    original = get_tier_config()
    again = parse_tier_config(original.to_json())
    assert again.to_json() == original.to_json()
    assert again.campaign_mix == original.campaign_mix


# -- the store: the file is the floor -----------------------------------------


async def _publish(session: AsyncSession, config: dict, *, version: int = 1) -> None:
    session.add(
        ChannelConfigVersion(version=version, config=config, status=ContentStatus.PUBLISHED)
    )
    await session.flush()


async def test_with_nothing_published_the_file_is_what_runs(session: AsyncSession):
    config = await resolve_config(session)
    assert config.to_json() == get_tier_config().to_json()


async def test_a_published_document_wins(session: AsyncSession):
    await _publish(session, _doc(whatsapp={"ladder": ["v2"], "enabled": False}))
    config = await resolve_config(session)
    assert not config.is_enabled(Channel.WHATSAPP)


async def test_the_newest_published_version_wins(session: AsyncSession):
    await _publish(session, _doc(kiosk={"ladder": ["v3"], "enabled": False}), version=1)
    await _publish(session, _doc(kiosk={"ladder": ["v3"], "enabled": True}), version=2)
    assert (await resolve_config(session)).is_enabled(Channel.KIOSK)


async def test_an_unparseable_published_row_falls_back_to_the_file_not_to_closed(
    session: AsyncSession,
):
    """The one failure mode that must never close the OPD. A tree that fails to
    parse costs a patient slightly older questions; a channel document that fails
    to parse decides whether anything answers at all."""
    await _publish(session, {"channels": {"kiosk": {"ladder": ["v9"]}}})
    config = await resolve_config(session)
    assert config.is_enabled(Channel.KIOSK)
    assert config.to_json() == get_tier_config().to_json()


# -- readiness: computed, not asserted ----------------------------------------


async def test_kiosk_and_app_need_no_vendor(settings: Settings):
    """Why go-live is kiosk-first (doc 12 §8): these two have nothing that can be
    unprovisioned — local voice, the browser's own speech, the zero-AI floor."""
    for channel in (Channel.KIOSK, Channel.APP):
        ready, why = readiness(channel, settings)
        assert ready and why == ""


async def test_whatsapp_without_meta_credentials_is_not_ready(settings: Settings):
    configured = settings.model_copy(
        update={"messaging_provider": "meta", "meta_whatsapp_token": "", "meta_phone_number_id": ""}
    )
    ready, why = readiness(Channel.WHATSAPP, configured)
    assert not ready
    assert "Meta credentials" in why

    with_creds = configured.model_copy(
        update={"meta_whatsapp_token": "tok", "meta_phone_number_id": "123"}
    )
    assert readiness(Channel.WHATSAPP, with_creds)[0]


async def test_phone_without_exotel_credentials_is_not_ready(settings: Settings):
    configured = settings.model_copy(update={"telephony_provider": "exotel"})
    ready, why = readiness(Channel.PHONE, configured)
    assert not ready
    assert "Exotel credentials" in why


async def test_a_fake_vendor_is_ready_locally_and_nowhere_else(settings: Settings):
    """The dev stack must keep working; a box that is not local must not treat a
    fake provider as a provisioned one."""
    assert readiness(Channel.WHATSAPP, settings)[0]
    boxed = settings.model_copy(update={"env": "pilot"})
    ready, why = readiness(Channel.WHATSAPP, boxed)
    assert not ready
    assert "still 'fake'" in why


async def test_credentials_cannot_be_asserted_by_switching_a_channel_on(settings: Settings):
    """The inversion doc 12 §7 asks for: going live must not depend on remembering
    to switch things off. A channel switched *on* with no vendor is still shut."""
    config = parse_tier_config(_doc(whatsapp={"ladder": ["v2"], "enabled": True}))
    boxed = settings.model_copy(update={"env": "pilot", "messaging_provider": "meta"})
    state = channel_state(config, Channel.WHATSAPP, boxed)
    assert state.enabled and not state.ready and not state.is_open


async def test_the_console_can_tell_the_two_reasons_apart(settings: Settings):
    off = channel_state(
        parse_tier_config(_doc(whatsapp={"ladder": ["v2"], "enabled": False})),
        Channel.WHATSAPP,
        settings,
    )
    assert off.reason == "switched off in the admin console"

    unprovisioned = channel_state(
        parse_tier_config(_doc(whatsapp={"ladder": ["v2"]})),
        Channel.WHATSAPP,
        settings.model_copy(update={"env": "pilot", "messaging_provider": "meta"}),
    )
    assert "Meta credentials" in unprovisioned.reason


# -- what the patient hears ---------------------------------------------------


async def test_every_closed_notice_exists_in_every_pilot_language():
    for channel in SWITCHABLE:
        lines = CLOSED_MESSAGE[channel]
        for lang in PILOT_LANGUAGES:
            assert lines.get(lang), f"{channel.value} has no {lang} closed notice"


async def test_the_patient_never_sees_the_operator_reason(settings: Settings):
    """ "no Meta credentials" is an answer for the operator. What a patient gets
    is where to go instead."""
    config = parse_tier_config(_doc(whatsapp={"ladder": ["v2"], "enabled": False}))
    with pytest.raises(ChannelClosed) as caught:
        require_open(config, Channel.WHATSAPP, settings, lang=Lang.HI)
    message = caught.value.message
    assert "credentials" not in message.lower()
    assert message == CLOSED_MESSAGE[Channel.WHATSAPP][Lang.HI]


# -- the gates ----------------------------------------------------------------


@pytest_asyncio.fixture
async def kiosk_only(session: AsyncSession) -> None:
    """The go-live state of doc 12 §8: kiosk open, everything else dark."""
    await _publish(
        session,
        {
            "channels": {
                "kiosk": {"ladder": ["v_oss", "v3"], "enabled": True},
                "phone": {"ladder": ["v2", "v3"], "enabled": False},
                "whatsapp": {"ladder": ["v2", "v3"], "enabled": False},
                "app": {"ladder": ["v2", "v3"], "enabled": False},
            },
            "admission": {"max_oss_sessions": 12},
        },
    )


async def test_a_closed_kiosk_refuses_a_start_civilly(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    await _publish(session, _doc(kiosk={"ladder": ["v3"], "enabled": False}))
    response = await client.post(
        "/kiosk/start", json={"chief_complaint": "bukhar hai", "lang": "hi", "dept_key": "MEDONC"}
    )
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "channel_closed"
    assert body["channel"] == "kiosk"
    assert body["detail"] == CLOSED_MESSAGE[Channel.KIOSK][Lang.HI]
    assert response.headers["Retry-After"]


async def test_the_kiosk_is_untouched_when_the_other_channels_are_dark(
    client: AsyncClient, session: AsyncSession, kiosk_only: None
):
    """The headline AC: with every channel but kiosk disabled, the kiosk does not
    notice."""
    from app.models.org import Hospital
    from tests import factories as f

    hospital: Hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    session.add(f.make_department(hospital, code="MEDONC", name="Medical Oncology"))
    await session.flush()

    response = await client.post(
        "/kiosk/start", json={"chief_complaint": "bukhar hai", "lang": "hi", "dept_key": "MEDONC"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "routed"


def _wa_message(text: str, *, wa_id: str = "919812300077", message_id: str = "m1") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": "A"}}],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": message_id,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


async def test_a_closed_whatsapp_answers_once_and_starts_no_intake(
    client: AsyncClient, settings: Settings, kiosk_only: None, providers: None
):
    """Meta still gets its 200 — a non-200 makes it redeliver forever — but the
    patient gets one sentence pointing at the desk, and no bot logic runs."""
    from app.providers.registry import get_messaging_provider

    messaging = get_messaging_provider(settings)

    first = await client.post("/whatsapp/webhook", json=_wa_message("hello"))
    assert first.status_code == 200
    assert first.json() == {"status": "channel_closed"}
    assert len(messaging.sent) == 1
    assert messaging.sent[0].text == CLOSED_MESSAGE[Channel.WHATSAPP][Lang.EN]
    # The greeting the open bot sends carries buttons; the refusal must not, or a
    # patient will tap her way into a channel that is shut.
    assert not messaging.sent[0].buttons

    second = await client.post(
        "/whatsapp/webhook", json=_wa_message("are you there", message_id="m2")
    )
    assert second.status_code == 200
    assert len(messaging.sent) == 1, "a shut channel repeated itself"


async def test_a_closed_app_intake_leaves_the_rest_of_the_app_working(
    client: AsyncClient, session: AsyncSession, settings: Settings, kiosk_only: None
):
    """Her care file, her queue position and her reminders do not start anything
    the OPD has to staff, so they keep working with app intake dark."""
    from tests import factories as f

    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    patient = f.make_patient(hospital)
    session.add(patient)
    await session.flush()

    from app.auth.tokens import create_patient_access_token

    token = create_patient_access_token(
        patient_id=patient.id,
        name=patient.name,
        hospital_id=patient.hospital_id,
        via="self",
        actor_phone=patient.phone,
        settings=settings,
    ).token
    headers = {"Authorization": f"Bearer {token}"}

    blocked = await client.post(
        "/patient/intake/start",
        json={"chief_complaint": "bukhar", "lang": "hi", "dept_key": "MEDONC"},
        headers=headers,
    )
    assert blocked.status_code == 503
    assert blocked.json()["channel"] == "app"

    assert (await client.get("/patient/me", headers=headers)).status_code == 200
    assert (await client.get("/patient/file", headers=headers)).status_code == 200


async def test_a_mid_intake_patient_is_not_cut_off_when_the_kiosk_closes(
    client: AsyncClient, session: AsyncSession
):
    """Closing a channel means "take no new ones", not "abandon whoever is mid
    sentence". Only `/start` is gated."""
    from app.models.org import Hospital
    from tests import factories as f

    hospital: Hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    session.add(f.make_department(hospital, code="MEDONC", name="Medical Oncology"))
    await session.flush()

    started = await client.post(
        "/kiosk/start", json={"chief_complaint": "bukhar hai", "lang": "hi", "dept_key": "MEDONC"}
    )
    session_id = started.json()["session_id"]

    await _publish(session, _doc(kiosk={"ladder": ["v3"], "enabled": False}))

    resumed = await client.get(f"/kiosk/{session_id}/next")
    assert resumed.status_code == 200
