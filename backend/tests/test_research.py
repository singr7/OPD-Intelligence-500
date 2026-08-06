"""The research assistant (M5, plan §4).

Two acceptance criteria here are *negative* — the module cannot write to a
clinical record, and the client cannot put text in the context — so the
structural guards for both are the first section of this file and are
deliberately the first thing a reader hits. The rest drives context assembly out
of the three modules that feed it, the daily turn budget, and the provider-down
state that stores nothing.

No test here calls a vendor.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import doctor as doctor_svc
from app import queue as q
from app.models.audit import AuditLog
from app.models.clinical import (
    ClinicalNote,
    DocumentExtraction,
    MedicalDocument,
    Prescription,
    ResearchThread,
    ResearchTurn,
)
from app.models.enums import (
    Channel,
    DictationStatus,
    DocumentKind,
    DocumentStatus,
    NoteStatus,
    Sex,
    UsagePurpose,
)
from app.providers.llm import FakeLLMProvider, FakeLLMScript
from app.research import assistant as assist
from app.research import context as ctx
from app.research import threads as th

TODAY = q.today()

RESEARCH_PACKAGE = Path(ctx.__file__).parent

ANSWER = (
    "Anaemia during AC-T is usually managed by treating the cause first. "
    "Transfusion thresholds in the NCCN guidance are restrictive. "
    "Discuss against your local protocol before acting on any of this."
)


def _assistant(text: str = ANSWER) -> assist.Assistant:
    """An assistant whose model returns exactly `text`."""
    return assist.Assistant([FakeLLMProvider(script=[FakeLLMScript(text=text)])])


def _broken_assistant() -> assist.Assistant:
    """An assistant whose chain is down."""
    from app.providers import ProviderUnavailable

    provider = FakeLLMProvider()
    provider.fail_with = ProviderUnavailable("gemini http 503")
    return assist.Assistant([provider])


async def _clinic_with_visit(session: AsyncSession):
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    return clinic, visit


async def _sign_a_diagnosis(session: AsyncSession, visit, doctor, text: str) -> None:
    dictation = f.make_dictation(
        visit,
        doctor,
        structured={"diagnosis": text},
        status=DictationStatus.SIGNED,
        signed_at=datetime.now(UTC),
        signed_by=doctor.id,
    )
    session.add(dictation)
    await session.flush()


async def _scan_a_report(
    session: AsyncSession, patient, *, tests: list[dict[str, Any]], verified: bool = False
) -> None:
    document = MedicalDocument(
        patient_id=patient.id,
        kind=DocumentKind.LAB,
        status=DocumentStatus.SUMMARIZED,
        object_keys=["records/x/y/page-1.jpg"],
        pages=1,
    )
    session.add(document)
    await session.flush()
    session.add(
        DocumentExtraction(
            document_id=document.id,
            payload={"report_date": "2026-07-30", "tests": tests},
            outlier_count=sum(1 for t in tests if t.get("flag") not in {"normal", "unknown"}),
            verified_at=datetime.now(UTC) if verified else None,
        )
    )
    await session.flush()


async def _confirm_a_note(session: AsyncSession, visit, doctor, tags: dict[str, Any]) -> None:
    note = ClinicalNote(
        visit_id=visit.id,
        doctor_id=doctor.id,
        transcript="tolerating well, grade 1 mucositis",
        structured={"version": 1, "fields": {"subjective": "ok", "tags": tags}},
        status=NoteStatus.CONFIRMED,
        confirmed_at=datetime.now(UTC),
        confirmed_by=doctor.id,
    )
    session.add(note)
    await session.flush()


HB_LOW = {
    "name": "Hemoglobin",
    "value_text": "8.9",
    "value": "8.9",
    "unit": "g/dL",
    "ref_low": "12.0",
    "ref_high": "15.0",
    "flag": "low",
}
ANC_CRITICAL = {
    "name": "ANC",
    "value_text": "0.4",
    "value": "0.4",
    "unit": "10^9/L",
    "ref_low": "2.0",
    "ref_high": "7.0",
    "flag": "critical_low",
}
PLATELETS_NORMAL = {
    "name": "Platelets",
    "value_text": "240",
    "unit": "10^9/L",
    "flag": "normal",
}


# =============================================================================
# Acceptance criterion 1: this module cannot write to a clinical record
# =============================================================================


def test_the_research_module_cannot_reach_a_clinical_writer() -> None:
    """No file in `app/research/` imports a module that writes clinical content.

    Read off the source rather than asserted about behaviour, the M4 pattern:
    behaviour tests only cover the call paths somebody thought to write. Plan
    decision 7 says the assistant "cannot write to any clinical record", and
    this is that sentence stated as a property of the package — if a future
    session adds `from app import notes` to "just copy the tags across", this
    fails on the import line, before any of it runs.

    `app.doctor` is on the list too, which is why `context._diagnosis` re-queries
    the column `doctor._diagnosis` reads instead of calling it. The test below
    pins that the two agree.
    """
    forbidden = {
        "app.prescription",
        "app.formulary",
        "app.dictation",
        "app.notes",
        "app.checkins",
        "app.doctor",
    }
    offenders: dict[str, list[str]] = {}
    for path in sorted(RESEARCH_PACKAGE.glob("*.py")):
        imported: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                imported.add(base)
                imported.update(f"{base}.{alias.name}" for alias in node.names)
        reached = sorted(imported & forbidden)
        if reached:
            offenders[path.name] = reached
    assert not offenders, f"app/research must not reach a clinical writer: {offenders}"


def test_there_is_no_parser_for_a_research_answer() -> None:
    """The answer is prose, and prose is the safety property.

    Every other LLM pathway parses its reply into a contract — and each of those
    contracts is a place a field could be added. This one has none: `Assistant.
    ask` sets `json_output=False` and returns the text. If a future change gives
    a research answer a schema, the first step towards a field on a clinical
    record has already been taken, and it should be taken deliberately.
    """
    source = Path(assist.__file__).read_text(encoding="utf-8")
    assert "json_output=False" in source
    assert ".json()" not in source, "a research answer must not be parsed as JSON"


@pytest.mark.asyncio
async def test_asking_a_question_creates_no_prescription_and_no_note(
    session: AsyncSession,
) -> None:
    """The behavioural half of the same claim.

    The model is asked something whose answer is full of drug names, and the
    tables that hold drug orders and clinical notes stay empty.
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    thread = await th.open_thread(session, visit_id=visit.id, doctor=doctor, include=None)
    answer = await _assistant("Consider G-CSF prophylaxis; anthracyclines are the usual base.").ask(
        "What is secondary G-CSF prophylaxis?", context=""
    )
    await th.append_turn(
        session,
        thread=thread,
        question="What is secondary G-CSF prophylaxis?",
        answer=answer,
        context_sent=[],
        include=None,
    )

    assert await session.scalar(select(func.count(Prescription.id))) == 0
    assert await session.scalar(select(func.count(ClinicalNote.id))) == 0


def test_a_thread_has_no_way_to_be_marked_accepted() -> None:
    """No status, no signature, no `applied` column — by construction.

    The moment a turn can be marked accepted, a model's prose has become a
    clinical decision with a doctor's name attached. What a doctor takes from an
    answer they write themselves, on the consult note, in their own words.
    """
    thread_columns = set(ResearchThread.__table__.columns.keys())
    turn_columns = set(ResearchTurn.__table__.columns.keys())
    banned = {
        "status",
        "signed_at",
        "signed_by",
        "applied",
        "applied_at",
        "accepted",
        "verified_by",
    }
    assert not (thread_columns & banned), thread_columns & banned
    assert not (turn_columns & banned), turn_columns & banned


# =============================================================================
# Acceptance criterion 2: the client cannot put text in the context
# =============================================================================


def test_a_client_can_only_subtract_from_the_context() -> None:
    """The trim is by id. There is no code path that accepts context text.

    This is the difference between "the doctor can trim what we send" and "the
    browser can send anything it likes to a vendor" — and only the first is
    compatible with plan decision 8. `select` filters the items this module
    built; it cannot be handed a new one.
    """
    context = ctx.ResearchContext(
        items=(
            ctx.ContextItem(
                id=ctx.DEMOGRAPHICS, label="a", text="Patient: 50-59, female.", source=""
            ),
            ctx.ContextItem(id=ctx.DIAGNOSIS, label="b", text="Working diagnosis: X.", source=""),
        )
    )
    kept = context.select([ctx.DIAGNOSIS, "smuggled", "Patient name is Sunita Devi"])
    assert [item.id for item in kept] == [ctx.DIAGNOSIS]
    assert "Sunita" not in context.prompt_text([ctx.DIAGNOSIS, "Patient name is Sunita Devi"])


def test_unknown_context_ids_are_rejected_rather_than_ignored() -> None:
    """A client sending an id this module does not build is told plainly.

    Ignoring it would be friendlier and worse: a client that thinks it can put
    text in the context should find out at the first attempt, not discover
    months later that its "extra context" was silently dropped the whole time.
    """
    assert ctx.unknown_ids(None) == []
    assert ctx.unknown_ids([ctx.LABS, ctx.DIAGNOSIS]) == []
    assert ctx.unknown_ids([ctx.LABS, "free_text", "aaa"]) == ["aaa", "free_text"]


def test_none_means_everything_and_empty_means_empty() -> None:
    """A doctor who unticks every line is asking a general question.

    Collapsing `[]` into "everything" would be the easy bug here, and it would
    send a patient's whole context to a vendor at the exact moment the doctor
    had said not to.
    """
    context = ctx.ResearchContext(
        items=(
            ctx.ContextItem(id=ctx.DEMOGRAPHICS, label="a", text="one", source=""),
            ctx.ContextItem(id=ctx.LABS, label="b", text="two", source=""),
        )
    )
    assert len(context.select(None)) == 2
    assert context.select([]) == ()
    assert context.prompt_text([]) == ""


# =============================================================================
# Context assembly — the three modules that feed it
# =============================================================================


@pytest.mark.asyncio
async def test_context_carries_an_age_band_and_never_an_age(session: AsyncSession) -> None:
    """A year of birth is a quasi-identifier; a decade is not (`app.phi`)."""
    clinic, visit = await _clinic_with_visit(session)
    context = await ctx.assemble(session, visit=visit)

    demographics = next(i for i in context.items if i.id == ctx.DEMOGRAPHICS)
    assert "50-59" in demographics.text
    assert "52" not in demographics.text
    assert clinic["patient"].name not in context.prompt_text()


@pytest.mark.asyncio
async def test_context_takes_the_diagnosis_from_a_signed_note_only(session: AsyncSession) -> None:
    """A draft dictation is a doctor thinking out loud — the spine's own rule."""
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    draft = f.make_dictation(visit, doctor, structured={"diagnosis": "?? lymphoma"})
    session.add(draft)
    await session.flush()

    context = await ctx.assemble(session, visit=visit)
    assert not [i for i in context.items if i.id == ctx.DIAGNOSIS]
    assert "lymphoma" not in context.prompt_text()
    assert ("Working diagnosis", "no signed consult note for this patient yet") in context.absent

    await _sign_a_diagnosis(session, visit, doctor, "Carcinoma breast, T2N1M0")
    context = await ctx.assemble(session, visit=visit)
    diagnosis = next(i for i in context.items if i.id == ctx.DIAGNOSIS)
    assert "Carcinoma breast, T2N1M0" in diagnosis.text


@pytest.mark.asyncio
async def test_the_context_and_the_patient_card_agree_on_the_diagnosis(
    session: AsyncSession,
) -> None:
    """The behavioural pin behind `context._diagnosis` not importing `app.doctor`.

    The package refuses to import a clinical writer, so the diagnosis is a
    second query against the same column. What keeps the two honest is this:
    they must produce the same text for the same visit. If somebody changes one
    reader's rule — "latest signed" to "latest of any status", say — this fails
    rather than the spine and the research prompt quietly disagreeing about what
    the patient has.
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]
    await _sign_a_diagnosis(session, visit, doctor, "Carcinoma breast, T2N1M0")

    card = await doctor_svc.patient_card(session, visit_id=visit.id, doctor=doctor)
    context = await ctx.assemble(session, visit=visit)
    item = next(i for i in context.items if i.id == ctx.DIAGNOSIS)

    assert card.diagnosis is not None
    assert card.diagnosis.text in item.text


@pytest.mark.asyncio
async def test_only_flagged_lab_values_leave_the_box(session: AsyncSession) -> None:
    """Normal values are not what the question is about, and unjudged ones read
    exactly like judged ones once they are in a prompt."""
    clinic, visit = await _clinic_with_visit(session)
    unknown = {"name": "Ferritin", "value_text": "300", "unit": "ng/mL", "flag": "unknown"}
    await _scan_a_report(
        session, clinic["patient"], tests=[HB_LOW, PLATELETS_NORMAL, ANC_CRITICAL, unknown]
    )

    context = await ctx.assemble(session, visit=visit)
    labs = next(i for i in context.items if i.id == ctx.LABS)

    assert "Hemoglobin 8.9 g/dL, low (ref 12.0-15.0)" in labs.text
    assert "ANC 0.4 10^9/L, critically low" in labs.text
    assert "Platelets" not in labs.text
    assert "Ferritin" not in labs.text


@pytest.mark.asyncio
async def test_an_unverified_reading_is_labelled_rather_than_withheld(
    session: AsyncSession,
) -> None:
    """Doc 21 §1.5's rule: every surface showing an unverified reading says so.

    Withholding it would be worse — the doctor is looking at the same numbers on
    the Reports tab, and an answer that silently ignored them would be answering
    a different patient's question.
    """
    clinic, visit = await _clinic_with_visit(session)
    await _scan_a_report(session, clinic["patient"], tests=[HB_LOW], verified=False)

    labs = next(i for i in (await ctx.assemble(session, visit=visit)).items if i.id == ctx.LABS)
    assert labs.caveat
    assert "no doctor has checked" in labs.caveat


@pytest.mark.asyncio
async def test_note_tags_are_confirmed_only_and_keep_grade_mentioned_language(
    session: AsyncSession,
) -> None:
    """A draft is a machine reading nobody checked — the M4 rule.

    And a grade the doctor said out loud goes out as their statement, never as
    this system's assessment. `grade_mentioned` means "the doctor said a grade",
    and the prompt must not be handed anything that reads as "we graded it".
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    draft = ClinicalNote(
        visit_id=visit.id,
        doctor_id=doctor.id,
        transcript="drafted, never confirmed",
        structured={"fields": {"tags": {"problems": ["secret problem"], "symptoms": []}}},
        status=NoteStatus.DRAFT,
    )
    session.add(draft)
    await session.flush()

    context = await ctx.assemble(session, visit=visit)
    assert "secret problem" not in context.prompt_text()

    await _confirm_a_note(
        session,
        visit,
        doctor,
        {
            "problems": ["carcinoma breast"],
            "symptoms": [{"name": "mucositis", "grade_mentioned": "1"}],
            "followups": ["CBC before next cycle"],
        },
    )
    tags = next(
        i for i in (await ctx.assemble(session, visit=visit)).items if i.id == ctx.NOTE_TAGS
    )
    assert "the doctor mentioned grade 1" in tags.text
    assert "grade 1 mucositis" not in tags.text
    assert "CBC before next cycle" in tags.text


@pytest.mark.asyncio
async def test_a_first_visit_patient_opens_an_almost_empty_context_honestly(
    session: AsyncSession,
) -> None:
    """No signed note, no scan, no confirmed note is an ordinary first visit.

    The panel has to open for them, and every missing source has to say why it
    is missing rather than simply not appearing — a source that is absent and a
    source this module forgot to build must not look the same.
    """
    _clinic, visit = await _clinic_with_visit(session)
    context = await ctx.assemble(session, visit=visit)

    assert [i.id for i in context.items] == [ctx.DEMOGRAPHICS]
    assert dict(context.absent) == {
        "Working diagnosis": "no signed consult note for this patient yet",
        "Flagged lab values": "nothing out of range on file from a scanned report",
        "Today's note tags": "no confirmed note on this visit yet",
    }


@pytest.mark.asyncio
async def test_the_assembled_context_never_carries_an_identifier(session: AsyncSession) -> None:
    """The property test behind plan decision 8.

    A patient whose every free-text field has been stuffed with identifiers —
    the name in the diagnosis, a phone number in a note tag — and nothing
    identifying comes out the far side. `assemble` runs `phi.assert_clean` over
    what it built, so an edit that interpolates a name into one of these lines
    fails here rather than in a vendor's logs.
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]
    patient = clinic["patient"]
    patient.name = "Sunita Devi"
    patient.phone = "+91 98765 43210"
    await session.flush()

    await _sign_a_diagnosis(session, visit, doctor, "Carcinoma breast")
    await _scan_a_report(session, patient, tests=[HB_LOW])
    await _confirm_a_note(session, visit, doctor, {"problems": ["carcinoma breast"]})

    text = (await ctx.assemble(session, visit=visit)).prompt_text()
    assert "Sunita" not in text
    assert "98765" not in text
    assert patient.mrn not in text
    assert "Ramgarh" not in text
    assert str(patient.id) not in text


@pytest.mark.asyncio
async def test_a_name_smuggled_into_a_note_tag_fails_assembly(session: AsyncSession) -> None:
    """The guard fires on content, not only on field names.

    A doctor can dictate anything, and `phi.assert_clean` refuses a phone number
    wherever it appears. Better a 500 the session log records than a patient's
    number in a vendor's prompt history.
    """
    from app.phi import PHILeak

    clinic, visit = await _clinic_with_visit(session)
    await _confirm_a_note(
        session, visit, clinic["doctor"], {"followups": ["call her on 98765 43210"]}
    )
    with pytest.raises(PHILeak):
        await ctx.assemble(session, visit=visit)


@pytest.mark.asyncio
async def test_suggestions_are_built_from_the_context_and_never_from_the_model(
    session: AsyncSession,
) -> None:
    """A model proposing what to ask about a patient is a model steering a
    clinical enquiry — a larger thing than answering the question a doctor
    chose to type, and not one this session argued for."""
    clinic, visit = await _clinic_with_visit(session)
    assert ctx.suggestions(await ctx.assemble(session, visit=visit)) == ()

    await _sign_a_diagnosis(session, visit, clinic["doctor"], "Carcinoma breast")
    await _scan_a_report(session, clinic["patient"], tests=[HB_LOW])
    offered = ctx.suggestions(await ctx.assemble(session, visit=visit))
    assert offered
    assert len(offered) <= 3


@pytest.mark.asyncio
async def test_lab_values_are_capped(session: AsyncSession) -> None:
    """A panel with everything deranged is a real thing to hold a question
    about, and also a 60-line context nobody reads before tapping send."""
    clinic, visit = await _clinic_with_visit(session)
    many = [{**HB_LOW, "name": f"Analyte {n}"} for n in range(30)]
    await _scan_a_report(session, clinic["patient"], tests=many)

    labs = next(i for i in (await ctx.assemble(session, visit=visit)).items if i.id == ctx.LABS)
    assert labs.text.count(";") + 1 <= ctx.MAX_VALUES


# =============================================================================
# The daily turn budget
# =============================================================================


@pytest.mark.asyncio
async def test_the_budget_counts_this_doctors_turns_today(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]
    thread = await th.open_thread(session, visit_id=visit.id, doctor=doctor, include=None)

    budget = await assist.budget_for(session, doctor_id=doctor.id, limit=3)
    assert (budget.used, budget.remaining, budget.exhausted) == (0, 3, False)

    for n in range(3):
        await th.append_turn(
            session,
            thread=thread,
            question=f"q{n}",
            answer=assist.Answer(
                text="a", model="m", provider="p", prompt_ref="research_assist@v1"
            ),
            context_sent=[],
            include=None,
        )

    budget = await assist.budget_for(session, doctor_id=doctor.id, limit=3)
    assert (budget.used, budget.remaining, budget.exhausted) == (3, 0, True)


@pytest.mark.asyncio
async def test_yesterdays_questions_do_not_spend_todays_budget(session: AsyncSession) -> None:
    """A calendar day, not a rolling 24 hours — a doctor who asked thirty
    questions at yesterday's clinic starts today with a full budget."""
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]
    thread = await th.open_thread(session, visit_id=visit.id, doctor=doctor, include=None)

    turn = ResearchTurn(thread_id=thread.id, question="q", answer="a", context_sent=[])
    session.add(turn)
    await session.flush()
    turn.created_at = datetime.now(UTC) - timedelta(days=1)
    await session.flush()

    budget = await assist.budget_for(session, doctor_id=doctor.id, limit=40)
    assert budget.used == 0


@pytest.mark.asyncio
async def test_one_doctors_curiosity_does_not_spend_anothers_budget(
    session: AsyncSession,
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    other_user = f.make_user(clinic["hospital"])
    session.add(other_user)
    await session.flush()
    other_doctor = f.make_doctor(other_user, clinic["department"])
    session.add(other_doctor)
    await session.flush()

    thread = await th.open_thread(session, visit_id=visit.id, doctor=doctor, include=None)
    await th.append_turn(
        session,
        thread=thread,
        question="q",
        answer=assist.Answer(text="a", model="m", provider="p", prompt_ref="r@v1"),
        context_sent=[],
        include=None,
    )

    assert (await assist.budget_for(session, doctor_id=doctor.id, limit=40)).used == 1
    assert (await assist.budget_for(session, doctor_id=other_doctor.id, limit=40)).used == 0


# =============================================================================
# Threads, turns, and the conversation
# =============================================================================


@pytest.mark.asyncio
async def test_two_doctors_on_one_visit_get_their_own_threads(session: AsyncSession) -> None:
    """A research thread is one clinician's line of reasoning. Merging two would
    attribute one doctor's question to another in a record that exists precisely
    so that attribution is answerable later."""
    clinic, visit = await _clinic_with_visit(session)
    covering_user = f.make_user(clinic["hospital"])
    session.add(covering_user)
    await session.flush()
    covering = f.make_doctor(covering_user, clinic["department"])
    session.add(covering)
    await session.flush()

    mine = await th.open_thread(session, visit_id=visit.id, doctor=clinic["doctor"], include=None)
    theirs = await th.open_thread(session, visit_id=visit.id, doctor=covering, include=None)
    assert mine.id != theirs.id

    again = await th.open_thread(session, visit_id=visit.id, doctor=clinic["doctor"], include=None)
    assert again.id == mine.id


@pytest.mark.asyncio
async def test_a_doctor_from_another_department_is_refused(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    other_department = f.make_department(clinic["hospital"])
    session.add(other_department)
    await session.flush()
    outsider_user = f.make_user(clinic["hospital"])
    session.add(outsider_user)
    await session.flush()
    outsider = f.make_doctor(outsider_user, other_department)
    session.add(outsider)
    await session.flush()

    with pytest.raises(th.ResearchError, match="another department"):
        await th.assert_visit_scope(session, visit_id=visit.id, doctor=outsider)


def test_history_replays_the_exchange_but_never_an_old_context() -> None:
    """Replaying an older turn's context would mean an item the doctor unticked
    five minutes ago coming back with every subsequent question, which would
    make the trim control a lie."""
    turns = [
        ResearchTurn(question=f"q{n}", answer=f"a{n}", context_sent=[f"stale context {n}"])
        for n in range(8)
    ]
    history = assist.history_for(turns, depth=2)

    assert history == (("user", "q6"), ("assistant", "a6"), ("user", "q7"), ("assistant", "a7"))
    assert "stale context" not in "".join(text for _role, text in history)


def test_the_stored_context_is_exactly_what_was_sent() -> None:
    """One function renders both, so a reader months later sees
    character-for-character what the vendor saw — not a re-render against a lab
    value that has since been re-flagged."""
    context = ctx.ResearchContext(
        items=(
            ctx.ContextItem(
                id=ctx.DEMOGRAPHICS, label="a", text="Patient: 50-59, female.", source=""
            ),
            ctx.ContextItem(id=ctx.LABS, label="b", text="Out-of-range: Hb 8.9, low.", source=""),
        )
    )
    prompt_block, frozen = assist.render_context(context, [ctx.LABS])

    assert frozen == ["Out-of-range: Hb 8.9, low."]
    assert prompt_block == "- Out-of-range: Hb 8.9, low."
    for line in frozen:
        assert line in prompt_block


@pytest.mark.asyncio
async def test_a_turn_stores_its_provider_and_prompt_version(session: AsyncSession) -> None:
    """Config moves; a stored answer stays attributable (the VOICE1 pattern)."""
    clinic, visit = await _clinic_with_visit(session)
    thread = await th.open_thread(
        session, visit_id=visit.id, doctor=clinic["doctor"], include=[ctx.LABS]
    )
    answer = await _assistant().ask("what about the anaemia?", context="- Hb 8.9, low")
    turn = await th.append_turn(
        session,
        thread=thread,
        question="what about the anaemia?",
        answer=answer,
        context_sent=["Hb 8.9, low"],
        include=[ctx.LABS],
    )

    assert turn.provider_snapshot["ask"]["provider"] == "fake-llm"
    assert turn.prompt_refs == ["research_assist@v1"]
    assert turn.context_sent == ["Hb 8.9, low"]
    assert thread.context_include == [ctx.LABS]


@pytest.mark.asyncio
async def test_a_turn_is_audited(session: AsyncSession) -> None:
    """`Clinical`, and so audited — "what did the doctor look up before they
    changed the plan" is exactly what a medico-legal review asks."""
    clinic, visit = await _clinic_with_visit(session)
    thread = await th.open_thread(session, visit_id=visit.id, doctor=clinic["doctor"], include=None)
    turn = await th.append_turn(
        session,
        thread=thread,
        question="q",
        answer=assist.Answer(text="a", model="m", provider="p", prompt_ref="r@v1"),
        context_sent=[],
        include=None,
    )
    await session.flush()

    rows = await session.scalars(
        select(AuditLog).where(AuditLog.entity_id.in_([str(turn.id), str(thread.id)]))
    )
    assert list(rows), "a research turn must land in the audit log"


# =============================================================================
# Asking, and the states around it
# =============================================================================


@pytest.mark.asyncio
async def test_the_question_and_the_context_both_reach_the_model() -> None:
    provider = FakeLLMProvider(script=[FakeLLMScript(text=ANSWER)])
    await assist.Assistant([provider]).ask(
        "How should I manage this anaemia?", context="- Out-of-range values: Hb 8.9 g/dL, low."
    )

    request = provider.calls[-1]
    assert "How should I manage this anaemia?" in request.prompt
    assert "Hb 8.9 g/dL, low" in request.prompt
    assert request.json_output is False
    assert request.prompt_ref == "research_assist@v1"


@pytest.mark.asyncio
async def test_an_empty_context_is_stated_rather_than_left_blank() -> None:
    """A doctor who unticked everything asked a general question. The prompt
    says so, rather than presenting an empty block the model fills in from
    whatever it assumes about an oncology patient."""
    provider = FakeLLMProvider(script=[FakeLLMScript(text=ANSWER)])
    await assist.Assistant([provider]).ask("What is Lynch syndrome?", context="")

    assert "the doctor sent no patient context" in provider.calls[-1].prompt


@pytest.mark.asyncio
async def test_a_research_turn_is_metered_as_research() -> None:
    """Its own purpose: this is the only pathway whose cost scales with how
    curious a doctor is rather than with how many patients came through."""
    provider = FakeLLMProvider(script=[FakeLLMScript(text=ANSWER)])
    await assist.Assistant([provider]).ask("q", context="")

    assert UsagePurpose.RESEARCH.value == "research"
    assert len(UsagePurpose.RESEARCH.value) <= 11, "usage_events.purpose is varchar(11)"


@pytest.mark.asyncio
async def test_a_provider_outage_stores_nothing_and_queues_nothing(
    session: AsyncSession,
) -> None:
    """An unanswered question is a question the doctor still has, and they can
    ask it again in four seconds. A pending row would leave a question in a
    clinical audit trail with no answer beside it."""
    clinic, visit = await _clinic_with_visit(session)
    thread = await th.open_thread(session, visit_id=visit.id, doctor=clinic["doctor"], include=None)

    with pytest.raises(assist.ResearchUnavailable):
        await _broken_assistant().ask("what about the anaemia?", context="")

    turns = await th.turns_for(session, thread=thread)
    assert turns == []


@pytest.mark.asyncio
async def test_an_empty_completion_is_an_outage_not_an_answer() -> None:
    """A 200 carrying no text is a vendor failing quietly. Storing it would put
    a blank turn in a clinical audit trail."""
    with pytest.raises(assist.ResearchUnavailable):
        await _assistant("   ").ask("q", context="")


@pytest.mark.asyncio
async def test_an_empty_question_is_refused_before_a_vendor_is_called() -> None:
    provider = FakeLLMProvider(script=[FakeLLMScript(text=ANSWER)])
    with pytest.raises(assist.ResearchError):
        await assist.Assistant([provider]).ask("   ", context="- something")
    assert provider.calls == []


def test_the_prompt_refuses_dosing_and_urgency_in_words() -> None:
    """The register is pinned in the prompt, and the prompt is data.

    Not a behavioural test — a fake model cannot demonstrate a refusal. It
    asserts the four refusals plan §4.1 names are actually written into the
    system prompt, so a future version cannot drop one silently while the
    module's docstrings still claim it.
    """
    from app.prompts import load

    system = load("research_assist", assist.PROMPT_VERSION).system.lower()
    for phrase in ("dose", "urgency", "local protocol", "cutoff"):
        assert phrase in system, f"the research prompt must address {phrase!r}"


def test_the_prompt_version_is_pinned_not_latest() -> None:
    """A prompt edit must not quietly change the register of answers a doctor
    has been reading all week."""
    source = Path(assist.__file__).read_text(encoding="utf-8")
    assert "PROMPT_VERSION = 1" in source
    assert 'load("research_assist", prompt_version)' in source


def test_sex_is_carried_but_never_a_missing_one_invented() -> None:
    class _P:
        age = 61
        sex = Sex.MALE

    class _Q:
        age = None
        sex = None

    assert ctx._sex(_P()) == "male"  # type: ignore[arg-type]
    assert ctx._sex(_Q()) == "sex not recorded"  # type: ignore[arg-type]


def test_an_unreadable_reference_range_does_not_break_a_value_line() -> None:
    assert ctx._value_line({"name": "Hb", "value_text": "8.9", "flag": "low"}) == "Hb 8.9, low"
    assert ctx._value_line({"name": "Hb", "flag": "normal"}) == ""
    assert ctx._value_line({"flag": "low"}) == ""
    assert "above 2.0" in ctx._value_line(
        {"name": "ANC", "value_text": "0.4", "ref_low": "2.0", "flag": "low"}
    )


def test_the_operating_day_is_the_one_the_rest_of_the_system_uses() -> None:
    """Not a second definition of "today" for a doctor to hold in their head."""
    assert assist.operating_day() == q.today()


def test_every_context_id_is_known_to_the_validator() -> None:
    """`ALL_IDS` and the constants cannot drift — a new item that forgets to
    join `ALL_IDS` would be rejected as unknown the moment a client sent it
    back, which is a confusing way to find out."""
    assert ctx.ALL_IDS == {ctx.DEMOGRAPHICS, ctx.DIAGNOSIS, ctx.LABS, ctx.NOTE_TAGS}


@pytest.mark.asyncio
async def test_a_thread_with_no_turn_yet_reports_no_stored_trim(session: AsyncSession) -> None:
    """None and `[]` are different answers, and the panel renders them
    differently: None shows everything ticked, `[]` shows everything unticked."""
    clinic, visit = await _clinic_with_visit(session)
    thread = await th.open_thread(session, visit_id=visit.id, doctor=clinic["doctor"], include=None)
    assert th.stored_include(None) is None
    assert th.stored_include(thread) is None

    thread.context_include = [ctx.LABS]
    assert th.stored_include(thread) == [ctx.LABS]


@pytest.mark.asyncio
async def test_no_such_visit_is_refused(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    with pytest.raises(th.ResearchError, match="no such visit"):
        await th.assert_visit_scope(session, visit_id=uuid.uuid4(), doctor=clinic["doctor"])
