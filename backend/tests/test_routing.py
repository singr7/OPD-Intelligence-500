"""Chief complaint → department (doc 03 §1a) and its eval harness (doc 06 S4).

**What is not tested here: whether the model is any good.** The AC is "classifier
≥85% on eval set", and that number can only come from a real vendor answering real
calls — which has never happened in this repo (STATE.md). Scripting the fake with
the right answers and asserting 100% would be a green tick measuring nothing,
while reading like the AC was met. So:

- the accuracy of a *model* is measured by `python -m app.evals`, live, by a human
  with a key (see HANDOFF);
- the accuracy arithmetic, the labels, and everything the classifier does with what
  a model returns are tested here.

The second list is where the patient safety actually lives. A classifier is the
one place a model's judgement reaches a patient, so the tests below are mostly
about distrusting it: invented departments, missing confidence, junk JSON, and
outages all have to land the patient at a desk with a human rather than on the
wrong floor.
"""

from __future__ import annotations

import json

import pytest

from app.evals import (
    EvalError,
    EvalSet,
    Outcome,
    check_labels,
    load_eval,
    run_eval,
)
from app.models.enums import Lang, UsagePurpose
from app.providers.llm import FakeLLMProvider, FakeLLMScript
from app.providers.resilience import ProviderBadRequest, ProviderUnavailable
from app.routing import (
    CONFIDENCE_FLOOR,
    TRIAGE_DEPARTMENT,
    DepartmentGuess,
    DepartmentOption,
    classify_department,
    pilot_departments,
)
from app.trees.bank import load_bank


def reply(dept_key: str, confidence: float = 0.9, reason: str = "test") -> FakeLLMScript:
    return FakeLLMScript(
        text=json.dumps({"dept_key": dept_key, "confidence": confidence, "reason": reason})
    )


def fake(*scripts: FakeLLMScript) -> FakeLLMProvider:
    return FakeLLMProvider(script=list(scripts))


async def classify(complaint: str, *scripts: FakeLLMScript, **kwargs) -> DepartmentGuess:
    return await classify_department(complaint, providers=[fake(*scripts)], **kwargs)


# -- the departments it may choose ---------------------------------------------


def test_the_pilot_departments_come_from_the_seed():
    options = pilot_departments()
    assert {option.key for option in options} == {
        "MEDONC",
        "RADONC",
        "SURGONC",
        "PALL",
        "GENMED",
        "GYNAE",
        "ENT",
        "PULM",
        "DERM",
    }


def test_every_department_it_can_choose_has_a_tree_to_ask():
    """Routing to a department with no tree is a patient at a desk with nothing to
    ask them."""
    bank_departments = {tree.department for tree in load_bank().values()}
    assert {option.key for option in pilot_departments()} <= bank_departments


def test_departments_render_for_the_prompt():
    option = DepartmentOption(key="MEDONC", name="Medical Oncology", note="chemo")
    assert option.render() == "- MEDONC: Medical Oncology — chemo"
    assert DepartmentOption(key="ENT", name="ENT").render() == "- ENT: ENT"


async def test_routing_with_no_departments_is_a_deployment_bug_not_a_shrug():
    with pytest.raises(ValueError, match="no departments"):
        await classify_department("kuch bhi", departments=[], providers=[fake()])


# -- the happy path ------------------------------------------------------------


async def test_a_confident_answer_is_taken():
    guess = await classify("kimo ke liye aaya hoon", reply("MEDONC", 0.95, "came for chemo"))
    assert guess.dept_key == "MEDONC"
    assert guess.confidence == 0.95
    assert guess.reason == "came for chemo"
    assert guess.needs_human is False
    assert guess.from_model is True


async def test_the_complaint_and_the_departments_reach_the_prompt():
    provider = fake(reply("MEDONC"))
    await classify_department("सिकाई के लिए आया हूँ", lang=Lang.HI, providers=[provider])
    request = provider.last
    assert "सिकाई के लिए आया हूँ" in request.prompt
    assert "- MEDONC: Medical Oncology" in request.prompt
    assert "hi" in request.prompt


async def test_the_call_is_stamped_with_the_prompt_version():
    """An output has to be traceable to the exact prompt that produced it."""
    provider = fake(reply("MEDONC"))
    await classify_department("kimo", providers=[provider])
    assert provider.last.prompt_ref == "routing@v1"
    assert provider.last.json_output is True


async def test_routing_is_deterministic_not_creative():
    """The same sentence must reach the same desk on Tuesday as on Monday."""
    provider = fake(reply("MEDONC"))
    await classify_department("kimo", providers=[provider])
    assert provider.last.temperature == 0.0


async def test_routing_cost_is_attributed_to_routing():
    """doc 03 §11 separates routing cost from intake-turn cost, and S18 cannot
    recover the distinction later."""
    provider = fake(reply("MEDONC"))
    await classify_department("kimo", providers=[provider])
    # `purpose` rides on the provider call, not on usage_scope.
    assert provider.calls, "the classifier never called the provider"


async def test_a_code_fenced_reply_is_still_understood():
    """Models wrap JSON in ``` despite being told not to. Failing a patient's
    intake over three backticks is a bad trade."""
    fenced = FakeLLMScript(
        text='```json\n{"dept_key": "PULM", "confidence": 0.8, "reason": "cough"}\n```'
    )
    guess = await classify("khansi", fenced)
    assert guess.dept_key == "PULM"


# -- distrusting the model -----------------------------------------------------


async def test_an_invented_department_sends_the_patient_to_triage():
    """Told to pick from nine keys, a model still occasionally returns a tenth."""
    guess = await classify("kuch samajh nahi aa raha", reply("ONCOLOGY", 0.99))
    assert guess.dept_key == TRIAGE_DEPARTMENT
    assert guess.needs_human is True
    assert guess.from_model is False
    assert "ONCOLOGY" in guess.reason


async def test_low_confidence_asks_a_human_rather_than_guessing():
    """The prompt: "Below 0.6, a human coordinator will check your answer — that is
    a good outcome, not a failure"."""
    guess = await classify("shareer me dard", reply("GENMED", 0.4))
    assert guess.dept_key == "GENMED"
    assert guess.needs_human is True
    # It is still the model's answer — just one nobody should act on alone.
    assert guess.from_model is True


async def test_the_confidence_floor_is_where_the_prompt_says_it_is():
    assert CONFIDENCE_FLOOR == 0.6
    assert (await classify("x", reply("ENT", 0.6))).needs_human is False
    assert (await classify("x", reply("ENT", 0.59))).needs_human is True


async def test_a_missing_confidence_is_not_high_confidence():
    script = FakeLLMScript(text=json.dumps({"dept_key": "ENT", "reason": "lump"}))
    guess = await classify("gale me gaanth", script)
    assert guess.dept_key == "ENT"
    assert guess.confidence == 0.0
    assert guess.needs_human is True


@pytest.mark.parametrize("confidence", ["high", None, True, [0.9]])
async def test_a_non_numeric_confidence_is_not_trusted(confidence):
    script = FakeLLMScript(text=json.dumps({"dept_key": "ENT", "confidence": confidence}))
    guess = await classify("gale me gaanth", script)
    assert guess.needs_human is True


async def test_an_out_of_range_confidence_is_clamped():
    assert (await classify("x", reply("ENT", 4.2))).confidence == 1.0
    assert (await classify("x", reply("ENT", -1.0))).confidence == 0.0


async def test_junk_json_sends_the_patient_to_triage_not_to_an_error():
    guess = await classify("kuch bhi", FakeLLMScript(text="I think it's oncology?"))
    assert guess.dept_key == TRIAGE_DEPARTMENT
    assert guess.needs_human is True
    assert guess.from_model is False


async def test_a_json_array_is_not_an_answer():
    guess = await classify("kuch bhi", FakeLLMScript(text='["MEDONC"]'))
    assert guess.dept_key == TRIAGE_DEPARTMENT
    assert guess.needs_human is True


# -- degrade, never deny -------------------------------------------------------


async def test_an_outage_is_a_triage_referral_not_an_exception():
    """doc 02 §5. A patient standing at a kiosk cannot be told the AI is down."""
    provider = fake()
    provider.fail_with = ProviderUnavailable("gemini is down")

    guess = await classify_department("kimo ke liye aaya hoon", providers=[provider])
    assert guess.dept_key == TRIAGE_DEPARTMENT
    assert guess.needs_human is True
    assert guess.from_model is False
    assert "unavailable" in guess.reason


async def test_a_bad_request_also_lands_the_patient_at_a_desk():
    provider = fake()
    provider.fail_with = ProviderBadRequest("malformed")

    guess = await classify_department("kimo", providers=[provider])
    assert guess.dept_key == TRIAGE_DEPARTMENT
    assert guess.needs_human is True


async def test_the_fallback_provider_gets_a_turn_before_triage():
    """doc 02 §2's Gemini→OpenAI chain has to actually be used."""
    primary = fake()
    primary.fail_with = ProviderUnavailable("down")
    secondary = fake(reply("RADONC", 0.9))

    guess = await classify_department("sikai", providers=[primary, secondary])
    assert guess.dept_key == "RADONC"
    assert guess.from_model is True
    assert secondary.calls


# -- the eval set --------------------------------------------------------------


def test_the_eval_set_has_the_sixty_utterances_doc_06_asked_for():
    eval_set = load_eval("routing")
    assert len(eval_set.cases) == 60
    assert eval_set.threshold == 0.85


def test_every_eval_label_is_a_real_department():
    """A typo'd label is a case the classifier can never pass, dragging the score
    down for a reason that has nothing to do with the model."""
    check_labels(load_eval("routing"), pilot_departments())


def test_the_eval_set_covers_every_department():
    """A department with no cases is a routing path nobody ever measured."""
    eval_set = load_eval("routing")
    labelled = {case.expect for case in eval_set.cases}
    assert labelled == {option.key for option in pilot_departments()}


def test_the_eval_set_is_mostly_the_languages_patients_actually_use():
    eval_set = load_eval("routing")
    langs = {case.lang for case in eval_set.cases}
    assert Lang.HI in langs and Lang.EN in langs
    hindi = sum(1 for case in eval_set.cases if case.lang is Lang.HI)
    assert hindi >= 40, "an Alwar OPD is not an English-speaking room"


def test_the_eval_set_includes_the_vague_utterances_that_must_go_to_triage():
    """The prompt's hardest instruction is to *not* guess. If the set has no vague
    cases, nothing measures whether it obeys."""
    eval_set = load_eval("routing")
    triage = [case for case in eval_set.cases if case.expect == TRIAGE_DEPARTMENT]
    assert len(triage) >= 5


def test_eval_case_ids_are_unique_and_stable():
    eval_set = load_eval("routing")
    ids = [case.id for case in eval_set.cases]
    assert len(set(ids)) == len(ids)


def test_a_malformed_eval_set_is_loud(tmp_path):
    """A broken eval scores 0% silently, which reads as a broken model."""
    (tmp_path / "broken_eval.json").write_text(json.dumps({"cases": []}))
    with pytest.raises(EvalError, match="non-empty list"):
        load_eval("broken", root=tmp_path)

    (tmp_path / "dup_eval.json").write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "a", "utterance": "x", "expect": "ENT"},
                    {"id": "a", "utterance": "y", "expect": "ENT"},
                ]
            }
        )
    )
    with pytest.raises(EvalError, match="duplicate case id"):
        load_eval("dup", root=tmp_path)

    (tmp_path / "nolabel_eval.json").write_text(
        json.dumps({"cases": [{"id": "a", "utterance": "x"}]})
    )
    with pytest.raises(EvalError, match="'expect'"):
        load_eval("nolabel", root=tmp_path)


def test_a_label_naming_a_department_that_does_not_exist_is_rejected():
    bogus = EvalSet(
        id="x",
        description="",
        threshold=0.85,
        cases=(
            # EvalCase via load is validated; construct directly for the label check.
            __import__("app.evals", fromlist=["EvalCase"]).EvalCase(
                id="a", lang=Lang.HI, utterance="x", expect="UROLOGY"
            ),
        ),
    )
    with pytest.raises(EvalError, match="do not exist"):
        check_labels(bogus, pilot_departments())


def test_missing_eval_set_is_loud(tmp_path):
    with pytest.raises(EvalError, match="no eval set"):
        load_eval("nope", root=tmp_path)


# -- the harness arithmetic ----------------------------------------------------
#
# These score a fake whose answers we chose, so they say nothing about any model.
# They exist because a harness that miscounts would make the real run — the one
# with a vendor key that decides the AC — a lie in either direction.


def outcome(expect: str, got: str, confidence: float = 0.9) -> Outcome:
    from app.evals import EvalCase

    return Outcome(
        case=EvalCase(id=expect, lang=Lang.HI, utterance="x", expect=expect),
        guess=DepartmentGuess(
            dept_key=got,
            confidence=confidence,
            reason="",
            needs_human=confidence < CONFIDENCE_FLOOR,
        ),
    )


def report_of(*outcomes: Outcome):
    from app.evals import EvalReport

    return EvalReport(
        eval_set=EvalSet(id="t", description="", threshold=0.85, cases=()),
        outcomes=outcomes,
        provider="fake",
    )


def test_accuracy_counts_what_it_says():
    report = report_of(
        outcome("MEDONC", "MEDONC"),
        outcome("ENT", "ENT"),
        outcome("PULM", "DERM"),
        outcome("PALL", "PALL"),
    )
    assert report.accuracy == 0.75
    assert len(report.failures) == 1


def test_the_threshold_gates():
    passing = report_of(*[outcome("ENT", "ENT")] * 9, outcome("ENT", "DERM"))
    assert passing.accuracy == 0.9
    assert passing.passed is True

    failing = report_of(*[outcome("ENT", "ENT")] * 8, *[outcome("ENT", "DERM")] * 2)
    assert failing.accuracy == 0.8
    assert failing.passed is False


def test_an_empty_run_scores_zero_rather_than_dividing_by_nothing():
    assert report_of().accuracy == 0.0
    assert report_of().passed is False


def test_a_low_confidence_miss_is_not_counted_as_confidently_wrong():
    """The distinction the whole design rests on: a hand-off to a coordinator is a
    good outcome; a confident wrong answer walks someone up the wrong stairs."""
    report = report_of(
        outcome("MEDONC", "DERM", confidence=0.2),  # wrong, but asked for help
        outcome("ENT", "PULM", confidence=0.95),  # wrong, and sure
    )
    assert len(report.failures) == 2
    assert len(report.confidently_wrong) == 1
    assert report.confidently_wrong[0].case.expect == "ENT"
    assert len(report.handed_to_a_human) == 1


def test_the_confusion_matrix_names_the_pairs_that_went_wrong():
    report = report_of(
        outcome("ENT", "PULM"),
        outcome("ENT", "PULM"),
        outcome("MEDONC", "RADONC"),
        outcome("PALL", "PALL"),
    )
    assert report.confusion()[("ENT", "PULM")] == 2
    assert report.confusion()[("MEDONC", "RADONC")] == 1
    assert ("PALL", "PALL") not in report.confusion()


def test_the_summary_says_which_provider_produced_the_number():
    """An accuracy figure without the provider behind it is not a result — the
    fake will happily score 100%."""
    summary = report_of(outcome("ENT", "ENT")).summary()
    assert "fake" in summary
    assert "accuracy" in summary


async def test_the_harness_drives_the_classifier_end_to_end():
    """Plumbing only: the fake answers from a script, so the accuracy here is a
    property of the script, not of any model."""
    eval_set = load_eval("routing")
    scripted = fake(*[reply(case.expect) for case in eval_set.cases])

    report = await run_eval(eval_set, providers=[scripted], provider_label="fake-llm")
    assert len(report.outcomes) == 60
    assert len(scripted.calls) == 60
    assert report.accuracy == 1.0  # by construction — the fake was told the answers


async def test_the_harness_reports_a_real_miss():
    eval_set = load_eval("routing")
    answers = [reply(case.expect) for case in eval_set.cases]
    answers[0] = reply("DERM")  # r01 "kimo ke liye aaya hoon" is MEDONC

    report = await run_eval(eval_set, providers=[fake(*answers)], provider_label="fake-llm")
    assert report.accuracy == pytest.approx(59 / 60)
    assert report.failures[0].case.id == "r01"
    assert report.confusion()[("MEDONC", "DERM")] == 1


async def test_the_harness_survives_a_provider_that_dies_mid_run():
    """60 sequential calls against a real vendor will sometimes hit one. The run
    should score it as a triage referral, not abort at case 31."""
    eval_set = load_eval("routing")
    dead = fake()
    dead.fail_with = ProviderUnavailable("down")

    report = await run_eval(eval_set, providers=[dead], provider_label="fake-llm")
    assert len(report.outcomes) == 60
    assert all(o.guess.needs_human for o in report.outcomes)
    assert all(not o.guess.from_model for o in report.outcomes)


def test_usage_purpose_routing_exists_for_this():
    assert UsagePurpose.ROUTING == "routing"
