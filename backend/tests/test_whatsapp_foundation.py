"""WhatsApp channel foundation (S12, doc 03 §1d): templates, the 24h window, and
node rendering. The bot flow and the webhook are tested separately; this file
covers the pieces they stand on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import Lang
from app.providers.messaging import OutboundMessage
from app.whatsapp import conversation as conv
from app.whatsapp import render
from app.whatsapp import templates as tpl

# -- templates ----------------------------------------------------------------


def test_a_registered_template_fills_its_placeholders_in_order():
    template = tpl.get_template("token_status", Lang.EN)
    filled = template.preview(["Ramesh", "City Hospital", "42", "3 patients"])
    assert (
        filled
        == "Namaste Ramesh. Your token today at City Hospital is 42. 3 patients ahead of you."
    )


def test_every_template_carries_both_pilot_languages():
    for name in {n for (n, _lang) in tpl._REGISTRY}:
        for lang in (Lang.EN, Lang.HI):
            # Raises TemplateError if a language is missing — an out-of-window
            # message the patient cannot read is worse than none.
            tpl.get_template(name, lang)


def test_a_missing_template_is_an_error_not_a_silent_english_fallback():
    with pytest.raises(tpl.TemplateError):
        tpl.get_template("no_such_template", Lang.HI)


def test_the_wrong_number_of_variables_is_caught_before_the_wire():
    with pytest.raises(tpl.TemplateError, match="wants 4 variables, got 1"):
        tpl.template_message(
            to="919876543210", name="token_status", lang=Lang.EN, variables=["only-one"]
        )


def test_template_message_builds_a_template_shaped_outbound():
    message = tpl.template_message(
        to="919876543210",
        name="prescription_ready",
        lang=Lang.HI,
        variables=["Ramesh", "Anil Gupta", "City Hospital"],
    )
    assert message.template_name == "prescription_ready"
    assert message.template_lang == "hi"
    assert message.template_variables == ("Ramesh", "Anil Gupta", "City Hospital")
    assert message.text == ""  # a template send carries no free text


def test_a_body_variable_count_mismatch_fails_at_construction():
    with pytest.raises(tpl.TemplateError):
        tpl.Template(
            name="broken", lang=Lang.EN, category="UTILITY", body="Hi {{1}} {{2}}", variables=("a",)
        )


# -- the 24h window -----------------------------------------------------------


def test_a_thread_never_heard_from_is_out_of_window():
    # Conservative: the first proactive contact must go by template, not free text.
    c = conv.Conversation(wa_id="919876543210")
    assert c.within_window() is False


def test_a_recent_inbound_opens_the_window_and_an_old_one_closes_it():
    now = datetime.now(UTC)
    c = conv.Conversation(wa_id="919876543210")
    c.mark_inbound(now=now)
    assert c.within_window(now=now + timedelta(hours=23)) is True
    assert c.within_window(now=now + timedelta(hours=25)) is False


def test_reset_flow_keeps_identity_and_window_but_drops_the_live_session():
    c = conv.Conversation(
        wa_id="919876543210",
        step=conv.ConversationStep.INTAKE,
        lang=Lang.HI,
        session_id="sess-1",
    )
    c.mark_inbound()
    c.reset_flow()
    assert c.session_id is None
    assert c.step is conv.ConversationStep.IDLE
    assert c.lang is Lang.HI  # a returning patient is not re-asked their language
    assert c.last_inbound_at is not None


def test_conversation_round_trips_through_json():
    c = conv.Conversation(
        wa_id="919876543210",
        step=conv.ConversationStep.DEPARTMENT,
        lang=Lang.EN,
        session_id="sess-2",
        department_options=[["MEDONC", "Medical Oncology"]],
    )
    c.mark_inbound()
    again = conv.Conversation.from_json(c.to_json())
    assert again.wa_id == c.wa_id
    assert again.step is conv.ConversationStep.DEPARTMENT
    assert again.department_options == [["MEDONC", "Medical Oncology"]]
    assert again.within_window() is True


async def test_the_in_memory_store_hands_back_copies():
    store = conv.InMemoryConversationStore()
    c = conv.Conversation(wa_id="wa1", lang=Lang.HI)
    await store.save(c)
    fetched = await store.get("wa1")
    assert fetched is not None and fetched.lang is Lang.HI
    # Mutating the fetched copy must not change the stored one without a save().
    fetched.lang = Lang.EN
    again = await store.get("wa1")
    assert again.lang is Lang.HI


# -- rendering a node ---------------------------------------------------------


def _option_node(n: int) -> dict:
    return {
        "id": "q1",
        "type": "single",
        "text": "Aap ko kya takleef hai?",
        "options": [{"id": f"o{i}", "text": f"Option {i}", "icon": None} for i in range(n)],
    }


def test_three_or_fewer_options_render_as_buttons():
    message = render.render_question("wa1", _option_node(3), Lang.HI)
    assert isinstance(message, OutboundMessage)
    assert [b.id for b in message.buttons] == ["o0", "o1", "o2"]
    assert not message.list_rows


def test_four_or_more_options_render_as_a_list():
    message = render.render_question("wa1", _option_node(5), Lang.HI)
    assert not message.buttons
    assert [r.id for r in message.list_rows] == ["o0", "o1", "o2", "o3", "o4"]


def test_a_long_option_label_is_truncated_to_metas_button_cap():
    node = {
        "id": "q1",
        "type": "single",
        "text": "?",
        "options": [{"id": "o1", "text": "A very very long option label indeed", "icon": None}],
    }
    message = render.render_question("wa1", node, Lang.EN)
    assert len(message.buttons[0].title) <= 20


def test_a_number_node_prompts_for_a_number_with_its_range():
    node = {
        "id": "q2",
        "type": "number",
        "text": "Bukhar kitne din se?",
        "min": 0,
        "max": 30,
        "unit": "days",
    }
    message = render.render_question("wa1", node, Lang.EN)
    assert not message.buttons and not message.list_rows
    assert "0–30 days" in message.text
