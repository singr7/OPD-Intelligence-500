"""The protocol bank, and the mistakes it must refuse to load (doc 03 §9).

Two jobs. The first half asserts the shipped bank is complete and sane — every
family reachable, every string in four languages, every grading rule able to
fire. The second half is the important one: a malformed bank has to fail at
**load**, not at 03:00 when a patient's D+7 message goes out ungraded. Same
stance as `app.trees.schema` (S4), which is why the grading rules go through that
module's own validator rather than a second dialect.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.checkins.protocols import (
    PROTOCOLS_PATH,
    QUESTION_TYPES,
    ProtocolError,
    get_bank,
    parse,
)
from app.languages import PILOT_LANGUAGES
from app.models.enums import CheckinGrade
from app.trees import rules as rule_lang


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(PROTOCOLS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def payload(raw: dict) -> dict:
    """A deep copy per test, so a mutation for one rejection case cannot leak."""
    return copy.deepcopy(raw)


# -- the shipped bank ---------------------------------------------------------


def test_the_six_regimen_families_doc_06_names_all_exist() -> None:
    bank = get_bank()
    assert set(bank.protocols) == {
        "platinum",
        "taxane",
        "anthracycline",
        "radiotherapy",
        "post_op",
        "palliative",
    }


def test_every_protocol_asks_something_more_than_once() -> None:
    """A single check-in is a text message, not continuity of care."""
    for protocol in get_bank().protocols.values():
        assert len(protocol.checkins) >= 2, protocol.key


def test_every_patient_facing_string_is_in_all_four_languages() -> None:
    bank = get_bank()
    for protocol in bank.protocols.values():
        assert set(protocol.label) == set(PILOT_LANGUAGES), protocol.key
    for qset in bank.question_sets.values():
        assert set(qset.title) == set(PILOT_LANGUAGES), qset.key
        for question in qset.questions:
            assert set(question.prompt) == set(PILOT_LANGUAGES), question.id
            for option in question.options:
                assert set(option.label) == set(PILOT_LANGUAGES), option.id


def test_grading_reasons_are_english_staff_text_not_patient_text() -> None:
    """The nurse queue reads these; a patient never does. Same call the queue
    board makes for its priority chips."""
    indic = range(0x0900, 0x0D80)  # Devanagari through Telugu
    for qset in get_bank().question_sets.values():
        for rule in qset.grading:
            assert not any(ord(c) in indic for c in rule.reason), (qset.key, rule.id)


def test_every_question_set_can_reach_both_grades() -> None:
    """A set with only amber rules cannot escalate, and a set with only red rules
    escalates everything. Both read as reviewed and are not."""
    for qset in get_bank().question_sets.values():
        grades = {rule.grade for rule in qset.grading}
        assert grades == {CheckinGrade.RED, CheckinGrade.AMBER}, qset.key


def test_every_grading_rule_addresses_a_question_in_its_own_set() -> None:
    for qset in get_bank().question_sets.values():
        ids = set(qset.question_ids)
        for rule in qset.grading:
            referenced = rule_lang.referenced_nodes(rule.when)
            assert referenced, (qset.key, rule.id)
            assert referenced <= ids, (qset.key, rule.id, referenced - ids)


def test_no_grading_rule_matches_a_free_voice_answer() -> None:
    """The S4 boundary, carried forward: a rule over ASR output makes a clinical
    escalation depend on the transcriber. `parse` enforces it; this says why."""
    bank = get_bank()
    for qset in bank.question_sets.values():
        free = {q.id for q in qset.questions if q.type == "free_voice"}
        for rule in qset.grading:
            assert not (rule_lang.referenced_nodes(rule.when) & free), (qset.key, rule.id)


def test_question_types_are_answerable_on_a_keypad_or_three_buttons() -> None:
    bank = get_bank()
    for qset in bank.question_sets.values():
        for question in qset.questions:
            assert question.type in QUESTION_TYPES
            if question.type == "single":
                # WhatsApp reply buttons cap at three; a check-in that needs a
                # list is a check-in a patient abandons.
                assert 2 <= len(question.options) <= 3, question.id


def test_cycled_regimens_have_a_cycle_length_and_the_others_do_not() -> None:
    bank = get_bank()
    assert bank.protocol("platinum").cycle_days == 21
    assert bank.protocol("taxane").cycle_days == 21
    assert bank.protocol("anthracycline").cycle_days == 21
    for key in ("radiotherapy", "post_op", "palliative"):
        assert bank.protocol(key).cycle_days == 0, key


def test_the_prompt_payload_hides_the_grading_rules_from_the_model() -> None:
    payload = get_bank().prompt_payload("platinum")
    assert "grading" not in json.dumps(payload)
    assert [c["day_offset"] for c in payload["checkins"]] == [2, 7, 14]


def test_get_bank_is_cached_so_a_live_checkin_never_re_reads_disk() -> None:
    assert get_bank() is get_bank()


# -- what the loader must refuse ----------------------------------------------


def test_rejects_a_question_set_no_protocol_uses(payload: dict) -> None:
    payload["question_sets"]["orphan"] = copy.deepcopy(payload["question_sets"]["myelosuppression"])
    with pytest.raises(ProtocolError, match="no protocol uses"):
        parse(payload)


def test_rejects_a_grading_rule_against_a_free_voice_question(payload: dict) -> None:
    payload["question_sets"]["palliative_comfort"]["grading"].append(
        {
            "id": "pall.words",
            "grade": "red",
            "reason": "Patient said something alarming",
            "when": {"op": "eq", "node": "ck.pall.other", "value": "bleeding"},
        }
    )
    with pytest.raises(rule_lang.RuleError):
        parse(payload)


def test_rejects_a_grading_rule_against_a_question_that_does_not_exist(payload: dict) -> None:
    payload["question_sets"]["myelosuppression"]["grading"][0]["when"] = {
        "op": "eq",
        "node": "ck.myelo.nonexistent",
        "value": "yes",
    }
    with pytest.raises(rule_lang.RuleError):
        parse(payload)


def test_rejects_a_green_grading_rule(payload: dict) -> None:
    """Green is the absence of a fired rule. A green rule would let a bank author
    quietly cancel a red one by ordering."""
    payload["question_sets"]["myelosuppression"]["grading"][0]["grade"] = "green"
    with pytest.raises(ProtocolError, match="'red' or 'amber'"):
        parse(payload)


def test_rejects_a_question_set_with_no_grading_at_all(payload: dict) -> None:
    payload["question_sets"]["myelosuppression"]["grading"] = []
    with pytest.raises(ProtocolError, match="non-empty 'grading'"):
        parse(payload)


def test_rejects_a_missing_language(payload: dict) -> None:
    del payload["question_sets"]["myelosuppression"]["questions"][0]["prompt"]["te"]
    with pytest.raises(ProtocolError, match="missing te"):
        parse(payload)


def test_rejects_an_unknown_option_set(payload: dict) -> None:
    payload["question_sets"]["myelosuppression"]["questions"][0]["options"] = "tri_state"
    with pytest.raises(ProtocolError, match="unknown option set"):
        parse(payload)


def test_rejects_a_number_question_with_no_bounds(payload: dict) -> None:
    del payload["question_sets"]["myelosuppression"]["questions"][1]["min"]
    with pytest.raises(ProtocolError, match="needs min and max"):
        parse(payload)


def test_rejects_two_checkins_on_the_same_day(payload: dict) -> None:
    payload["protocols"]["platinum"]["checkins"].append(
        {"day_offset": 2, "question_set": "myelosuppression"}
    )
    with pytest.raises(ProtocolError, match="two check-ins on day 2"):
        parse(payload)


def test_rejects_a_checkin_on_the_day_of_treatment(payload: dict) -> None:
    payload["protocols"]["platinum"]["checkins"][0]["day_offset"] = 0
    with pytest.raises(ProtocolError, match="positive integer"):
        parse(payload)


def test_rejects_a_protocol_that_matches_nothing(payload: dict) -> None:
    payload["protocols"]["palliative"]["match"] = {"drug_classes": [], "keywords": []}
    with pytest.raises(ProtocolError, match="matches nothing"):
        parse(payload)


def test_rejects_an_uppercase_match_keyword(payload: dict) -> None:
    """Matching is done on a lowercased haystack, so an uppercase keyword is a
    rule that silently never fires."""
    payload["protocols"]["platinum"]["match"]["keywords"].append("Cisplatin")
    with pytest.raises(ProtocolError, match="must be a lowercase string"):
        parse(payload)


def test_rejects_a_protocol_pointing_at_a_question_set_that_does_not_exist(
    payload: dict,
) -> None:
    payload["protocols"]["platinum"]["checkins"][0]["question_set"] = "gi_platinuum"
    with pytest.raises(ProtocolError, match="unknown question set"):
        parse(payload)
