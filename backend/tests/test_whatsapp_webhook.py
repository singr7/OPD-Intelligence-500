"""The Meta webhook (S12, doc 03 §1d) — verification, signatures, and the parse
from Meta's envelope into the bot. The bot flow itself is covered in
`test_whatsapp_bot.py`; here the concern is the HTTP edge Meta talks to.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.providers.registry import get_messaging_provider
from app.routes import whatsapp as wh
from tests import factories as f


@pytest_asyncio.fixture
async def seeded(session: AsyncSession):
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    for code, name in [("MEDONC", "Medical Oncology"), ("DERM", "Dermatology")]:
        session.add(f.make_department(hospital, code=code, name=name))
    await session.flush()
    return hospital


# -- GET verification ---------------------------------------------------------


async def test_verify_echoes_the_challenge_on_a_matching_token(
    settings: Settings, session: AsyncSession
):
    tokened = settings.model_copy(update={"meta_verify_token": "s3cr3t"})
    resp = await wh.verify(
        mode="subscribe", token="s3cr3t", challenge="12345", settings=tokened, session=session
    )
    assert resp.body == b"12345"
    assert resp.media_type == "text/plain"


async def test_verify_rejects_a_wrong_token(settings: Settings, session: AsyncSession):
    tokened = settings.model_copy(update={"meta_verify_token": "s3cr3t"})
    with pytest.raises(Exception) as exc:  # HTTPException(403)
        await wh.verify(
            mode="subscribe", token="wrong", challenge="1", settings=tokened, session=session
        )
    assert getattr(exc.value, "status_code", None) == 403


# -- signature ----------------------------------------------------------------


def test_a_body_meta_did_not_sign_is_rejected_when_a_secret_is_set():
    raw = b'{"entry": []}'
    good = "sha256=" + hmac.new(b"appsecret", raw, hashlib.sha256).hexdigest()
    # A correct signature passes.
    wh._verify_signature(raw, good, "appsecret")
    # A wrong one is a 403.
    with pytest.raises(Exception) as exc:
        wh._verify_signature(raw, "sha256=deadbeef", "appsecret")
    assert getattr(exc.value, "status_code", None) == 403


def test_signature_check_is_skipped_with_no_secret():
    # A local fake stack signs nothing; there is nothing to verify against.
    wh._verify_signature(b"anything", None, "")


# -- parsing ------------------------------------------------------------------


def test_parse_pulls_text_button_and_audio_out_of_metas_envelope():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "9199", "profile": {"name": "Ramesh"}}],
                            "messages": [
                                {
                                    "from": "9199",
                                    "id": "m1",
                                    "type": "text",
                                    "text": {"body": "hi"},
                                },
                                {
                                    "from": "9199",
                                    "id": "m2",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": "lang:en", "title": "English"},
                                    },
                                },
                                {
                                    "from": "9199",
                                    "id": "m3",
                                    "type": "audio",
                                    "audio": {"id": "media-1"},
                                },
                            ],
                        }
                    }
                ]
            }
        ]
    }
    parsed = wh._parse_inbound(payload)
    assert [m.kind for m in parsed] == ["text", "reply", "audio"]
    assert parsed[0].profile_name == "Ramesh"
    assert parsed[1].reply_id == "lang:en"
    assert parsed[2].media_id == "media-1"


def test_parse_ignores_status_callbacks():
    payload = {
        "entry": [{"changes": [{"value": {"statuses": [{"id": "s", "status": "delivered"}]}}]}]
    }
    assert wh._parse_inbound(payload) == []


# -- end to end through the ASGI app ------------------------------------------


async def test_a_text_message_drives_the_bot_and_sends_a_reply(
    client: AsyncClient, settings: Settings, seeded, providers: None
):
    messaging = get_messaging_provider(settings)
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "919812300077", "profile": {"name": "A"}}],
                            "messages": [
                                {
                                    "from": "919812300077",
                                    "id": "mm1",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    resp = await client.post("/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    # The bot answered with the language prompt, sent through the provider fake.
    assert messaging.sent
    assert any(m.buttons for m in messaging.sent)


async def test_the_webhook_always_200s_even_on_a_junk_body(client: AsyncClient, providers: None):
    resp = await client.post("/whatsapp/webhook", content=b"not json")
    assert resp.status_code == 200
