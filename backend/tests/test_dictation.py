"""Dictation → structured mapping (S10, doc 03 §7).

The session's acceptance criterion is a safety property, so most of this file is
one table-driven suite over `tests/fixtures/dictations.json`: ten Hinglish
consult notes, each paired with a *plausibly wrong* model output. The fixtures
are wrong on purpose — one renames a drug, one hallucinates one, one asserts
`known: true` for something that does not exist — because a fixture set where
the model behaves proves only that the happy path works.

The rest drives the record's state machine (start → map → correct → sign) and
the HTTP surface, with the LLM faked. No test here calls a vendor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import dictation as dic
from app import formulary as formulary_mod
from app import queue as q
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.audit import AuditLog
from app.models.clinical import Dictation
from app.models.enums import Channel, DictationStatus, Role
from app.providers.llm import FakeLLMProvider, FakeLLMScript

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "dictations.json").read_text(encoding="utf-8")
)
CASES: list[dict[str, Any]] = FIXTURES["cases"]
CASE_IDS = [case["id"] for case in CASES]

TODAY = q.today()


def _mapper(payload: dict[str, Any]) -> dic.DictationMapper:
    """A mapper whose model returns exactly `payload`."""
    provider = FakeLLMProvider(script=[FakeLLMScript(text=json.dumps(payload))])
    return dic.DictationMapper([provider])


async def _clinic_with_visit(session: AsyncSession):
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    return clinic, visit


# =============================================================================
# The acceptance criterion: ten Hinglish dictations, zero silent substitutions
# =============================================================================


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_fixture_maps_without_rewriting_a_single_drug_name(case: dict[str, Any]) -> None:
    """Every `name` that survives validation is byte-identical to the model's.

    This is the S10 AC stated as an invariant rather than a spot check: the
    validated mapping's drug names, in order, equal the raw model output's drug
    names, in order, for all ten fixtures — including the ones where a fuzzy
    match was sitting right there.
    """
    raw = dic.DictationMapping.parse(case["model_output"])
    validated = dic.validate_meds(raw, transcript=case["transcript"])

    assert [m.name for m in validated.meds] == [m.name for m in raw.meds]


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_fixture_flags_match_the_formulary(case: dict[str, Any]) -> None:
    """`known` comes from the book, not from the model's claim."""
    expect = case["expect"]
    mapping = dic.validate_meds(
        dic.DictationMapping.parse(case["model_output"]), transcript=case["transcript"]
    )

    assert len(mapping.meds) == expect["med_count"]
    assert [m.name for m in mapping.meds if m.known] == expect["known_names"]
    assert [m.name for m in mapping.meds if not m.known] == expect["unknown_names"]


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_fixture_flags_names_the_doctor_did_not_say(case: dict[str, Any]) -> None:
    """The rename detector, across the whole set.

    Two fixtures should fire it and eight should not — if this ever flags a
    fixture where the doctor plainly said the drug, the heuristic has started
    crying wolf and doctors will learn to tap through it.
    """
    mapping = dic.validate_meds(
        dic.DictationMapping.parse(case["model_output"]), transcript=case["transcript"]
    )
    assert [m.name for m in mapping.meds if m.unsaid] == case["expect"]["unsaid_names"]


def test_the_fixture_set_actually_contains_the_failures_it_claims_to() -> None:
    """Guards the guard: a fixture set that quietly became all-happy-path would
    make every test above pass while proving nothing."""
    all_expect = [case["expect"] for case in CASES]
    assert sum(len(e["unknown_names"]) for e in all_expect) >= 4, "no off-formulary drugs"
    assert sum(len(e["unsaid_names"]) for e in all_expect) >= 2, "no renamed/invented drugs"
    assert any(e.get("ambiguous_names") for e in all_expect), "no look-alike case"
    assert len(CASES) == 10


def test_a_helpfully_corrected_drug_name_is_caught() -> None:
    """d2 in detail: the doctor said "Vinblastin", the model wrote "vinblastine".

    The corrected name is a real formulary drug, so `known` is True and the
    formulary has nothing to complain about — this is precisely the substitution
    that a formulary check alone cannot see. It is caught by holding the name up
    against the doctor's own words.
    """
    case = next(c for c in CASES if c["id"] == "d2-model-corrects-a-drug")
    mapping = dic.validate_meds(
        dic.DictationMapping.parse(case["model_output"]), transcript=case["transcript"]
    )
    med = mapping.meds[0]

    assert med.name == "vinblastine"
    assert med.known is True  # the formulary is happy — and that is the problem
    assert med.unsaid is True  # this is what saves the patient
    assert "Vinblastin" in case["transcript"]
    assert med in mapping.meds_needing_attention


def test_a_hallucinated_drug_is_caught_twice() -> None:
    """d4: the model invented a drug and claimed `known: true` for it."""
    case = next(c for c in CASES if c["id"] == "d4-hallucinated-drug")
    mapping = dic.validate_meds(
        dic.DictationMapping.parse(case["model_output"]), transcript=case["transcript"]
    )
    invented = next(m for m in mapping.meds if m.name == "Tab Ondanzelin 8 mg")

    assert case["model_output"]["meds"][3]["known"] is True  # what the model said
    assert invented.known is False  # what the formulary says
    assert invented.unsaid is True  # and it was never spoken either


def test_a_lookalike_name_offers_both_candidates_and_picks_neither() -> None:
    """d7: `Tab Lukeran` sits between chlorambucil and melphalan."""
    case = next(c for c in CASES if c["id"] == "d7-lookalike-ambiguous")
    mapping = dic.validate_meds(
        dic.DictationMapping.parse(case["model_output"]), transcript=case["transcript"]
    )
    med = next(m for m in mapping.meds if m.name == "Tab Lukeran")

    assert med.known is False
    assert med.ambiguous is True
    assert {s["generic"] for s in med.suggestions} == {"chlorambucil", "melphalan"}
    assert med.generic is None  # nothing was chosen on the doctor's behalf


def test_a_missing_dose_stays_missing() -> None:
    """d6: no dose was spoken, so no dose is written. A standard dose filled in
    silently is the same class of harm as a corrected name."""
    case = next(c for c in CASES if c["id"] == "d6-missing-dose-stays-missing")
    mapping = dic.validate_meds(
        dic.DictationMapping.parse(case["model_output"]), transcript=case["transcript"]
    )
    assert all(m.dose is None for m in mapping.meds)
    assert mapping.unclear


def test_a_nameless_med_line_is_dropped_not_carried() -> None:
    """d9: a blank `name` cannot be reviewed and must not reach a prescription."""
    case = next(c for c in CASES if c["id"] == "d9-nameless-line-dropped")
    assert any(m["name"] == "" for m in case["model_output"]["meds"])
    mapping = dic.DictationMapping.parse(case["model_output"])
    assert len(mapping.meds) == 4
    assert all(m.name for m in mapping.meds)


def test_a_consult_that_prescribes_nothing_is_valid() -> None:
    """d10. A mapper that always produces drugs is a mapper that invents them."""
    case = next(c for c in CASES if c["id"] == "d10-no-meds-at-all")
    mapping = dic.validate_meds(
        dic.DictationMapping.parse(case["model_output"]), transcript=case["transcript"]
    )
    assert mapping.meds == ()
    assert mapping.meds_needing_attention == ()
    assert mapping.diagnosis


# =============================================================================
# The mapper (the provider-chain adapter)
# =============================================================================


async def test_the_mapper_sends_the_pinned_prompt_and_the_formulary() -> None:
    case = CASES[0]
    provider = FakeLLMProvider(script=[FakeLLMScript(text=json.dumps(case["model_output"]))])
    mapper = dic.DictationMapper([provider])

    result = await mapper.map(
        case["transcript"], patient="Ramesh, 54y, male", context="visit date: 2026-07-23"
    )

    assert result.prompt_ref == "dictation_map@v1"
    request = provider.last
    assert request is not None
    assert request.json_output is True
    assert request.temperature == 0.0
    assert case["transcript"] in request.prompt
    # The book is in the prompt for the model's own `known` reasoning, even
    # though we overrule it afterwards.
    assert "carboplatin [cytotoxic-platinum]" in request.prompt


async def test_the_mapper_runs_on_whatever_llm_is_configured() -> None:
    """The seam the operator cares about: the same code maps on Gemini Flash or
    on the box's Qwen3 (`LLM_PROVIDER=local_vllm`), because the mapper only ever
    sees an `LLMProvider` chain."""
    from app.providers.local_oss import LocalLLMProvider

    provider = LocalLLMProvider(base_url="http://10.8.0.2:8000/v1", model="qwen3-8b-awq")
    mapper = dic.DictationMapper([provider])
    assert mapper is not None  # constructing it is the assertion: no vendor coupling


async def test_the_mapper_falls_back_down_the_chain() -> None:
    case = CASES[0]
    primary = FakeLLMProvider()
    primary.fail_with = RuntimeError("boom")
    secondary = FakeLLMProvider(script=[FakeLLMScript(text=json.dumps(case["model_output"]))])

    result = await dic.DictationMapper([primary, secondary]).map(
        case["transcript"], patient="p", context="c"
    )
    assert len(result.mapping.meds) == 3


async def test_an_empty_transcript_is_refused_before_any_model_call() -> None:
    provider = FakeLLMProvider()
    with pytest.raises(dic.DictationError):
        await dic.DictationMapper([provider]).map("   ", patient="p", context="c")
    assert provider.calls == []


# =============================================================================
# The record: start -> map -> correct -> sign
# =============================================================================


async def test_start_is_idempotent_per_visit(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    first = await dic.start(session, visit_id=visit.id, doctor=clinic["doctor"], transcript="one")
    second = await dic.start(session, visit_id=visit.id, doctor=clinic["doctor"], transcript="two")

    assert first.id == second.id
    assert second.transcript == "two"
    rows = (await session.scalars(select(Dictation).where(Dictation.visit_id == visit.id))).all()
    assert len(rows) == 1


async def test_a_visit_in_another_department_is_refused(session: AsyncSession) -> None:
    clinic, _ = await _clinic_with_visit(session)
    other_dept = f.make_department(clinic["hospital"], code="RADONC")
    session.add(other_dept)
    await session.flush()
    elsewhere = f.make_visit(clinic["patient"], other_dept, date=TODAY, channel=Channel.KIOSK)
    session.add(elsewhere)
    await session.flush()

    with pytest.raises(dic.DictationError, match="another department"):
        await dic.start(session, visit_id=elsewhere.id, doctor=clinic["doctor"])


async def test_mapping_stores_both_the_model_version_and_the_working_copy(
    session: AsyncSession,
) -> None:
    case = CASES[0]
    clinic, visit = await _clinic_with_visit(session)
    dictation = await dic.start(
        session, visit_id=visit.id, doctor=clinic["doctor"], transcript=case["transcript"]
    )

    dictation = await dic.map_transcript(
        session,
        dictation=dictation,
        doctor=clinic["doctor"],
        mapper=_mapper(case["model_output"]),
    )

    structured = dictation.structured
    assert structured["mapped"] == structured["fields"]  # not yet corrected
    assert structured["prompt_ref"] == "dictation_map@v1"
    assert structured["model"] == "fake-llm-1"
    assert structured["version"] == dic.STRUCTURED_VERSION
    assert structured["edits"] == []
    assert structured["mapping_error"] is None


async def test_a_dead_llm_keeps_the_transcript(session: AsyncSession) -> None:
    """The recording is the irreplaceable half — the doctor has moved on."""
    clinic, visit = await _clinic_with_visit(session)
    dictation = await dic.start(
        session, visit_id=visit.id, doctor=clinic["doctor"], transcript="fever hai, Dolo 650 SOS"
    )
    dead = FakeLLMProvider()
    dead.fail_with = RuntimeError("vLLM is down")

    with pytest.raises(dic.MappingUnavailable):
        await dic.map_transcript(
            session,
            dictation=dictation,
            doctor=clinic["doctor"],
            mapper=dic.DictationMapper([dead]),
        )

    await session.refresh(dictation)
    assert dictation.transcript == "fever hai, Dolo 650 SOS"
    assert dictation.status is DictationStatus.DRAFT
    assert dictation.structured["mapping_error"]
    assert dictation.structured["fields"] is None  # nothing invented


async def test_corrections_land_on_fields_and_leave_the_model_version_alone(
    session: AsyncSession,
) -> None:
    case = CASES[0]
    clinic, visit = await _clinic_with_visit(session)
    dictation = await dic.start(
        session, visit_id=visit.id, doctor=clinic["doctor"], transcript=case["transcript"]
    )
    dictation = await dic.map_transcript(
        session, dictation=dictation, doctor=clinic["doctor"], mapper=_mapper(case["model_output"])
    )
    original_mapped = json.loads(json.dumps(dictation.structured["mapped"]))

    dictation = await dic.apply_corrections(
        session,
        dictation=dictation,
        doctor=clinic["doctor"],
        patch={"diagnosis": "Febrile neutropenia, post AC-T cycle 3"},
    )

    assert dictation.structured["mapped"] == original_mapped
    assert dictation.structured["fields"]["diagnosis"] == "Febrile neutropenia, post AC-T cycle 3"
    assert [e["field"] for e in dictation.structured["edits"]] == ["diagnosis"]
    assert dictation.structured["edits"][0]["from"] == original_mapped["diagnosis"]


async def test_a_drug_the_doctor_types_gets_the_same_verdict(session: AsyncSession) -> None:
    """Tap-to-fix is not an escape hatch: a typed name is looked up like any
    other, and is not quietly corrected either."""
    case = CASES[0]
    clinic, visit = await _clinic_with_visit(session)
    dictation = await dic.start(
        session, visit_id=visit.id, doctor=clinic["doctor"], transcript=case["transcript"]
    )
    dictation = await dic.map_transcript(
        session, dictation=dictation, doctor=clinic["doctor"], mapper=_mapper(case["model_output"])
    )

    dictation = await dic.apply_corrections(
        session,
        dictation=dictation,
        doctor=clinic["doctor"],
        patch={
            "meds": [
                {"name": "Tab Augmentin 625", "freq": "BD", "as_spoken": "Tab Augmentin 625 BD"},
                {
                    "name": "Tab Notarealdrug 10",
                    "freq": "OD",
                    "as_spoken": "Tab Notarealdrug 10 OD",
                },
            ]
        },
    )

    meds = dictation.structured["fields"]["meds"]
    assert meds[0]["name"] == "Tab Augmentin 625" and meds[0]["known"] is True
    assert meds[1]["name"] == "Tab Notarealdrug 10" and meds[1]["known"] is False


async def test_only_the_spec_fields_are_editable(session: AsyncSession) -> None:
    case = CASES[0]
    clinic, visit = await _clinic_with_visit(session)
    dictation = await dic.start(
        session, visit_id=visit.id, doctor=clinic["doctor"], transcript=case["transcript"]
    )
    dictation = await dic.map_transcript(
        session, dictation=dictation, doctor=clinic["doctor"], mapper=_mapper(case["model_output"])
    )

    with pytest.raises(dic.DictationError, match="not editable"):
        await dic.apply_corrections(
            session, dictation=dictation, doctor=clinic["doctor"], patch={"model": "gpt-5"}
        )


async def test_correcting_before_mapping_is_refused(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    dictation = await dic.start(session, visit_id=visit.id, doctor=clinic["doctor"], transcript="x")

    with pytest.raises(dic.DictationError, match="not been mapped"):
        await dic.apply_corrections(
            session, dictation=dictation, doctor=clinic["doctor"], patch={"diagnosis": "d"}
        )


# -- signing ------------------------------------------------------------------


async def _mapped(session: AsyncSession, case: dict[str, Any]):
    clinic, visit = await _clinic_with_visit(session)
    dictation = await dic.start(
        session, visit_id=visit.id, doctor=clinic["doctor"], transcript=case["transcript"]
    )
    dictation = await dic.map_transcript(
        session, dictation=dictation, doctor=clinic["doctor"], mapper=_mapper(case["model_output"])
    )
    return clinic, dictation


async def test_a_clean_note_signs(session: AsyncSession) -> None:
    clinic, dictation = await _mapped(session, CASES[0])

    signed = await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])

    assert signed.status is DictationStatus.SIGNED
    assert signed.signed_at is not None
    assert signed.signed_by == clinic["doctor"].id


async def test_an_unacknowledged_flag_blocks_signing(session: AsyncSession) -> None:
    case = next(c for c in CASES if c["id"] == "d3-off-formulary-drug")
    clinic, dictation = await _mapped(session, case)

    with pytest.raises(dic.DictationError, match="not been acknowledged"):
        await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])
    assert dictation.status is DictationStatus.DRAFT


async def test_acknowledging_an_off_formulary_drug_lets_it_sign(session: AsyncSession) -> None:
    """The formulary is incomplete by nature — the doctor must be able to
    prescribe past it, deliberately."""
    case = next(c for c in CASES if c["id"] == "d3-off-formulary-drug")
    clinic, dictation = await _mapped(session, case)
    meds = [dict(m, acknowledged=True) for m in dictation.structured["fields"]["meds"]]

    dictation = await dic.apply_corrections(
        session, dictation=dictation, doctor=clinic["doctor"], patch={"meds": meds}
    )
    signed = await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])

    assert signed.status is DictationStatus.SIGNED
    # Acknowledged, not resolved: it is still not on the formulary, and the
    # signed record says so.
    assert signed.structured["fields"]["meds"][0]["known"] is False


async def test_a_renamed_drug_blocks_signing_too(session: AsyncSession) -> None:
    case = next(c for c in CASES if c["id"] == "d2-model-corrects-a-drug")
    clinic, dictation = await _mapped(session, case)

    with pytest.raises(dic.DictationError, match="not been acknowledged"):
        await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])


async def test_signing_locks_the_record(session: AsyncSession) -> None:
    clinic, dictation = await _mapped(session, CASES[0])
    await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])

    with pytest.raises(dic.DictationLocked):
        await dic.apply_corrections(
            session, dictation=dictation, doctor=clinic["doctor"], patch={"diagnosis": "changed"}
        )
    with pytest.raises(dic.DictationLocked):
        await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])
    with pytest.raises(dic.DictationLocked):
        await dic.map_transcript(
            session,
            dictation=dictation,
            doctor=clinic["doctor"],
            mapper=_mapper(CASES[0]["model_output"]),
        )
    with pytest.raises(dic.DictationLocked):
        await dic.start(
            session, visit_id=dictation.visit_id, doctor=clinic["doctor"], transcript="again"
        )


async def test_an_unmapped_note_cannot_be_signed(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    dictation = await dic.start(session, visit_id=visit.id, doctor=clinic["doctor"], transcript="x")

    with pytest.raises(dic.DictationError, match="not been mapped"):
        await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])


async def test_every_dictation_write_is_audited(session: AsyncSession) -> None:
    """`Dictation` is a `Clinical` model, so this is inherited — asserted here
    because a signed prescription with no audit row is a compliance hole."""
    clinic, dictation = await _mapped(session, CASES[0])
    await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])
    await session.flush()

    rows = (await session.scalars(select(AuditLog).where(AuditLog.entity_id == dictation.id))).all()
    assert len(rows) >= 2  # insert + at least one update
    assert {str(r.entity) for r in rows} == {"dictations"}


# =============================================================================
# HTTP
# =============================================================================


def _headers(settings: Settings, user) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def test_routes_require_a_doctor(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    assert (await client.get(f"/dictation/visits/{visit.id}")).status_code == 401

    coordinator = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()
    resp = await client.get(
        f"/dictation/visits/{visit.id}", headers=_headers(settings, coordinator)
    )
    assert resp.status_code == 403


async def test_the_full_flow_over_http(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """start → map → correct → sign, as the console drives it."""
    case = next(c for c in CASES if c["id"] == "d3-off-formulary-drug")
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])

    fake = FakeLLMProvider(script=[FakeLLMScript(text=json.dumps(case["model_output"]))])
    monkeypatch.setattr("app.routes.dictation.llm_chain", lambda settings=None: [fake])

    resp = await client.get(f"/dictation/visits/{visit.id}", headers=headers)
    assert resp.status_code == 200 and resp.json() is None

    resp = await client.post(
        f"/dictation/visits/{visit.id}", json={"transcript": case["transcript"]}, headers=headers
    )
    assert resp.status_code == 200
    dictation_id = resp.json()["id"]
    assert resp.json()["status"] == "draft"

    resp = await client.post(f"/dictation/{dictation_id}/map", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["fields"]["meds"][0]["name"] == "Inj Ipilimumab 3 mg/kg"
    assert body["fields"]["meds"][0]["known"] is False
    assert body["blocking_meds"] == ["Inj Ipilimumab 3 mg/kg"]
    assert body["mapped"] == body["fields"]

    # Signing is refused while the flag stands.
    resp = await client.post(f"/dictation/{dictation_id}/sign", headers=headers)
    assert resp.status_code == 400

    meds = [dict(m, acknowledged=True) for m in body["fields"]["meds"]]
    resp = await client.patch(f"/dictation/{dictation_id}", json={"meds": meds}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["blocking_meds"] == []

    resp = await client.post(f"/dictation/{dictation_id}/sign", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "signed"
    assert resp.json()["signed_at"]

    # And it is locked.
    resp = await client.patch(
        f"/dictation/{dictation_id}", json={"diagnosis": "something else"}, headers=headers
    )
    assert resp.status_code == 409


async def test_a_dead_model_is_a_503_and_the_transcript_survives(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    dead = FakeLLMProvider()
    dead.fail_with = RuntimeError("no GPU")
    monkeypatch.setattr("app.routes.dictation.llm_chain", lambda settings=None: [dead])

    resp = await client.post(
        f"/dictation/visits/{visit.id}", json={"transcript": "Dolo 650 SOS"}, headers=headers
    )
    dictation_id = resp.json()["id"]

    resp = await client.post(f"/dictation/{dictation_id}/map", headers=headers)
    assert resp.status_code == 503

    resp = await client.get(f"/dictation/visits/{visit.id}", headers=headers)
    assert resp.json()["transcript"] == "Dolo 650 SOS"
    assert resp.json()["mapping_error"]


async def test_another_departments_note_is_refused_over_http(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, _ = await _clinic_with_visit(session)
    other_dept = f.make_department(clinic["hospital"], code="SURGONC")
    other_user = f.make_user(clinic["hospital"], role=Role.DOCTOR)
    session.add_all([other_dept, other_user])
    await session.flush()
    session.add(f.make_doctor(other_user, other_dept))
    elsewhere = f.make_visit(clinic["patient"], other_dept, date=TODAY, channel=Channel.KIOSK)
    session.add(elsewhere)
    await session.flush()

    resp = await client.get(
        f"/dictation/visits/{elsewhere.id}", headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 400
    assert "another department" in resp.json()["detail"]


async def test_stt_needs_a_doctor_unlike_the_kiosks(client: AsyncClient) -> None:
    """The kiosk's `/kiosk/stt` is open because a public terminal carries no
    credential and the clip is anonymous. This clip is a named patient's
    consult."""
    resp = await client.post("/dictation/stt", files={"file": ("a.webm", b"x", "audio/webm")})
    assert resp.status_code == 401


# =============================================================================
# The prompt
# =============================================================================


def test_the_mapping_prompt_forbids_substitution_in_so_many_words() -> None:
    """The prompt is the model's copy of this module's rule. If someone edits it
    into vagueness, the server-side check still holds — but the model starts
    guessing, and every guess costs the doctor an acknowledgement tap."""
    from app.prompts import load

    prompt = load("dictation_map", 1)
    system = prompt.system.lower()
    assert "never substitute" in system
    assert "known" in system
    assert "hinglish" in system
    assert set(prompt.variables) == {"transcript", "formulary_hint", "patient", "context"}


def test_the_formulary_hint_is_what_the_prompt_gets() -> None:
    hint = formulary_mod.get_formulary().prompt_hint()
    assert "morphine" in hint
    assert len(hint.splitlines()) == len(formulary_mod.get_formulary().drugs)
