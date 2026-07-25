"""The WhatsApp bot flow (S12, doc 03 §1d) — the second channel over the intake
engine, driven the way a webhook drives it: one message at a time, keyed by phone.

The fake department classifier cannot read its own reply (it answers `needs_human`
for every complaint), so every intake here goes through the department chooser —
which is also the flow that proves the chooser works. The happy path ends the same
as the kiosk's: a Visit with a token and a queue entry, on `Channel.WHATSAPP`.
"""

from __future__ import annotations

import itertools

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.intake import InMemorySessionStore, IntakeEngine
from app.models.clinical import Prescription, Visit
from app.models.enums import Channel
from app.models.org import Department, Hospital
from app.models.scheduling import QueueEntry
from app.providers.audio import AudioClip
from app.providers.registry import get_messaging_provider, get_tts_provider
from app.whatsapp.bot import Inbound, WhatsAppBot
from app.whatsapp.conversation import ConversationStep, InMemoryConversationStore
from tests import factories as f

pytestmark = pytest.mark.asyncio

_ids = itertools.count(1)


def _mid() -> str:
    return f"wamid.{next(_ids)}"


@pytest_asyncio.fixture
async def hospital(session: AsyncSession) -> Hospital:
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    for code, name in [("MEDONC", "Medical Oncology"), ("DERM", "Dermatology")]:
        session.add(f.make_department(hospital, code=code, name=name))
    await session.flush()
    return hospital


def _bot(settings: Settings) -> WhatsAppBot:
    return WhatsAppBot(
        engine=IntakeEngine(InMemorySessionStore()),
        conversations=InMemoryConversationStore(),
        settings=settings,
    )


async def _current_node(bot: WhatsAppBot, wa_id: str) -> dict | None:
    conv = await bot._conversations.get(wa_id)
    if conv is None or conv.session_id is None:
        return None
    state = await bot._engine.store.get(conv.session_id)
    dispatcher = bot._engine.dispatcher(state)
    return (await dispatcher.get_next_node()).get("node")


def _answer_for(wa_id: str, node: dict) -> Inbound:
    ntype = node["type"]
    if ntype in {"single", "multi", "body_map"}:
        return Inbound(
            wa_id=wa_id, kind="reply", reply_id=node["options"][0]["id"], message_id=_mid()
        )
    if ntype in {"number", "scale"}:
        value = node["min"] if node.get("min") is not None else 1
        return Inbound(wa_id=wa_id, kind="text", text=str(value), message_id=_mid())
    return Inbound(wa_id=wa_id, kind="text", text="pet mein dard hai", message_id=_mid())


async def _drive_to_token(bot: WhatsAppBot, session: AsyncSession, wa_id: str):
    for _ in range(100):
        conv = await bot._conversations.get(wa_id)
        if conv.step is ConversationStep.READBACK:
            return await bot.handle(
                session,
                Inbound(wa_id=wa_id, kind="reply", reply_id="confirm:yes", message_id=_mid()),
            )
        node = await _current_node(bot, wa_id)
        assert node is not None, "no question and not at read-back"
        await bot.handle(session, _answer_for(wa_id, node))
    raise AssertionError("intake did not reach the token")


# -- the buttons flow ---------------------------------------------------------


async def test_a_button_flow_completes_and_issues_a_token(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital
):
    bot = _bot(settings)
    wa = "919812300001"

    greet = await bot.handle(session, Inbound(wa_id=wa, kind="text", text="hi", message_id=_mid()))
    # First contact asks the language, as two buttons.
    assert greet.messages[0].buttons
    assert {b.id for b in greet.messages[0].buttons} == {"lang:en", "lang:hi"}

    await bot.handle(
        session, Inbound(wa_id=wa, kind="reply", reply_id="lang:en", message_id=_mid())
    )
    chooser = await bot.handle(
        session, Inbound(wa_id=wa, kind="text", text="stomach pain", message_id=_mid())
    )
    # The fake classifier is unsure → the department chooser (an interactive list).
    dept_msg = chooser.messages[-1]
    assert any(r.id == "dept:MEDONC" for r in dept_msg.list_rows)

    started = await bot.handle(
        session, Inbound(wa_id=wa, kind="reply", reply_id="dept:MEDONC", message_id=_mid())
    )
    assert started.messages  # the first tree question

    result = await _drive_to_token(bot, session, wa)
    assert result.queue_changed is True
    assert "token number" in result.messages[0].text.lower()

    visit = (
        await session.execute(
            select(Visit).where(Visit.channel == Channel.WHATSAPP, Visit.token_no.is_not(None))
        )
    ).scalar_one()
    assert visit.token_no is not None
    entry = (
        await session.execute(select(QueueEntry).where(QueueEntry.visit_id == visit.id))
    ).scalar_one()
    assert entry.token_no == visit.token_no


async def test_change_at_readback_restarts_the_intake(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital
):
    bot = _bot(settings)
    wa = "919812300009"
    await bot.handle(session, Inbound(wa_id=wa, kind="text", text="hi", message_id=_mid()))
    await bot.handle(
        session, Inbound(wa_id=wa, kind="reply", reply_id="lang:en", message_id=_mid())
    )
    await bot.handle(
        session, Inbound(wa_id=wa, kind="text", text="stomach pain", message_id=_mid())
    )
    await bot.handle(
        session, Inbound(wa_id=wa, kind="reply", reply_id="dept:MEDONC", message_id=_mid())
    )
    # Walk to read-back, then tap Change.
    for _ in range(100):
        conv = await bot._conversations.get(wa)
        if conv.step is ConversationStep.READBACK:
            break
        node = await _current_node(bot, wa)
        await bot.handle(session, _answer_for(wa, node))
    await bot.handle(
        session, Inbound(wa_id=wa, kind="reply", reply_id="confirm:no", message_id=_mid())
    )
    conv = await bot._conversations.get(wa)
    assert conv.step is ConversationStep.COMPLAINT
    assert conv.session_id is None  # the live session was dropped


# -- voice notes --------------------------------------------------------------


async def test_a_voice_note_chief_complaint_is_transcribed_and_routes(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital
):
    bot = _bot(settings)
    wa = "919812300002"
    # Preload the fake messaging provider's media so download_media resolves.
    messaging = get_messaging_provider(settings)
    messaging.media["voice-1"] = AudioClip(data=b"ogg", mime="audio/ogg")

    await bot.handle(session, Inbound(wa_id=wa, kind="text", text="namaste", message_id=_mid()))
    await bot.handle(
        session, Inbound(wa_id=wa, kind="reply", reply_id="lang:hi", message_id=_mid())
    )
    reply = await bot.handle(
        session, Inbound(wa_id=wa, kind="audio", media_id="voice-1", message_id=_mid())
    )
    # The voice note was transcribed (fake STT) and the complaint routed → chooser.
    assert any(r.id.startswith("dept:") for r in reply.messages[-1].list_rows)
    assert len(messaging.media) == 1  # download happened, not an upload


async def test_voice_note_replies_are_attached_when_enabled(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital
):
    voiced = settings.model_copy(update={"whatsapp_voice_notes": True})
    bot = _bot(voiced)
    wa = "919812300003"
    await bot.handle(session, Inbound(wa_id=wa, kind="text", text="hi", message_id=_mid()))
    # The complaint prompt (once a language is set) is read aloud.
    reply = await bot.handle(
        session, Inbound(wa_id=wa, kind="reply", reply_id="lang:en", message_id=_mid())
    )
    assert reply.voice_prompts
    await bot.synthesize_pending(reply)
    assert any(m.audio is not None for m in reply.messages)
    # The synthesized clip came from the TTS chain, not from nowhere.
    assert get_tts_provider(voiced) is not None


# -- commands -----------------------------------------------------------------


async def test_token_status_reports_position(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital
):
    from app import queue as q

    department = (
        await session.execute(select(Department).where(Department.code == "MEDONC"))
    ).scalar_one()
    patient = f.make_patient(hospital, phone="+919812300004")
    session.add(patient)
    await session.flush()
    visit = f.make_visit(patient, department, date=q.today(), token_no=7, channel=Channel.WHATSAPP)
    session.add(visit)
    await session.flush()
    intake = f.make_intake(visit, red_flags=[])
    session.add(intake)
    await session.flush()
    await q.enqueue_from_intake(session, visit=visit, intake=intake)

    bot = _bot(settings)
    reply = await bot.handle(
        session, Inbound(wa_id="919812300004", kind="text", text="status", message_id=_mid())
    )
    assert "7" in reply.messages[0].text


async def test_token_status_without_a_visit_is_honest(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital
):
    bot = _bot(settings)
    reply = await bot.handle(
        session, Inbound(wa_id="919800000000", kind="text", text="token", message_id=_mid())
    )
    # A brand-new thread has no language yet, so the honest "no token" reply is Hindi.
    assert "टोकन नहीं मिला" in reply.messages[0].text


async def test_resend_prescription_sends_the_latest(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital
):
    department = (
        await session.execute(select(Department).where(Department.code == "MEDONC"))
    ).scalar_one()
    patient = f.make_patient(hospital, phone="+919812300005")
    session.add(patient)
    await session.flush()
    visit = f.make_visit(patient, department, channel=Channel.WHATSAPP)
    session.add(visit)
    await session.flush()
    session.add(
        Prescription(
            visit_id=visit.id,
            meds=[{"name": "Tab Paracetamol", "dose": "500mg", "freq": "BD", "known": True}],
            delivered_via={},
        )
    )
    await session.flush()

    bot = _bot(settings)
    reply = await bot.handle(
        session, Inbound(wa_id="919812300005", kind="text", text="prescription", message_id=_mid())
    )
    assert "Paracetamol" in reply.messages[0].text


# -- redelivery ---------------------------------------------------------------


async def test_a_redelivered_message_is_ignored(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital
):
    bot = _bot(settings)
    wa = "919812300006"
    mid = _mid()
    first = await bot.handle(session, Inbound(wa_id=wa, kind="text", text="hi", message_id=mid))
    assert first.messages  # the language prompt
    again = await bot.handle(session, Inbound(wa_id=wa, kind="text", text="hi", message_id=mid))
    assert again.messages == []  # the exact replay did nothing


# -- one-tap appointment actions (S15, doc 03 §2) -----------------------------


async def _appointment_for(session: AsyncSession, hospital: Hospital, wa_id: str):
    """A patient reachable at `wa_id`, holding one booked appointment."""
    from datetime import UTC, datetime, timedelta

    from app import scheduling
    from app.models.enums import Channel as Ch
    from app.models.enums import Role

    department = (
        (await session.execute(select(Department).where(Department.hospital_id == hospital.id)))
        .scalars()
        .first()
    )
    user = f.make_user(hospital, role=Role.DOCTOR)
    session.add(user)
    await session.flush()
    doctor = f.make_doctor(user, department)
    patient = f.make_patient(hospital, phone=f"+{wa_id}")
    session.add_all([doctor, patient])
    await session.flush()

    starts = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=5, minute=0, second=0, microsecond=0
    )
    slot = f.make_slot(doctor, starts)
    session.add(slot)
    await session.flush()
    appointment = await scheduling.book(session, patient=patient, slot_id=slot.id, source=Ch.PHONE)
    return patient, appointment, slot


async def test_tapping_confirm_confirms_the_appointment(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital, sms
):
    from app.models.enums import AppointmentStatus

    wa_id = "919876500077"
    _, appointment, _ = await _appointment_for(session, hospital, wa_id)
    bot = _bot(settings)

    reply = await bot.handle(
        session,
        Inbound(
            wa_id=wa_id, kind="reply", reply_id=f"appt:confirm:{appointment.id}", message_id=_mid()
        ),
    )

    assert appointment.status is AppointmentStatus.CONFIRMED
    assert reply.messages, "the patient must be told the tap landed"


async def test_tapping_cancel_releases_the_seat(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital, sms
):
    from app.models.enums import AppointmentStatus

    wa_id = "919876500078"
    _, appointment, slot = await _appointment_for(session, hospital, wa_id)
    bot = _bot(settings)

    await bot.handle(
        session,
        Inbound(
            wa_id=wa_id, kind="reply", reply_id=f"appt:cancel:{appointment.id}", message_id=_mid()
        ),
    )

    assert appointment.status is AppointmentStatus.CANCELLED
    assert appointment.seat_no is None
    await session.refresh(slot)
    assert slot.booked == 0


async def test_a_tap_from_another_number_changes_nothing(
    session: AsyncSession, settings: Settings, providers: None, hospital: Hospital, sms
):
    """The button id travels inside a forwardable message; the number it arrives
    from is the authorisation."""
    from app.models.enums import AppointmentStatus

    _, appointment, _ = await _appointment_for(session, hospital, "919876500079")
    bot = _bot(settings)

    reply = await bot.handle(
        session,
        Inbound(
            wa_id="919000000000",
            kind="reply",
            reply_id=f"appt:cancel:{appointment.id}",
            message_id=_mid(),
        ),
    )

    from app.models.enums import Lang
    from app.whatsapp.bot import _APPT_UNKNOWN

    assert appointment.status is AppointmentStatus.BOOKED
    assert reply.messages[0].text == _APPT_UNKNOWN[Lang.HI]
