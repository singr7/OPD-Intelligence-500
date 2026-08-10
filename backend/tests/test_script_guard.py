"""The runtime script guard (`app.languages`, doc 03 §1).

The pilot's first live intake came back like this: the patient chose Hindi, the
recogniser returned their chief complaint in **Urdu script**, and the read-back —
the one confirmation a patient who cannot read a form ever gets — was a paragraph
in an alphabet they could not read either. It reached the coordinator console and
the board through the stored complaint.

Hindi and Urdu are one spoken language in two scripts, so a model asked for
"Hindi" answering in Perso-Arabic is not a malfunction it will announce. Nothing
downstream can detect it either: the text is well-formed, the confidence is high,
and the JSON is valid.

So the prompt asks (summarize v3 pins the script) and this guard *guarantees*.
The rule it enforces is deliberately narrow, because the two things that must
keep working are the two things a naive check breaks:

  * romanised Hinglish ("chest mein pain hai") is legitimate and stays;
  * Latin digits, units and acronyms inside Indic text stay.

What it rejects is a **non-Latin script the language does not use**. That is
decidable, total, and needs no model.

Every model boundary that can put words in front of a patient is covered here,
and `test_every_model_text_boundary_is_guarded` is the one that has to fail when
someone adds the sixth.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.languages import foreign_scripts, is_script_safe, script_problem
from app.models.enums import Lang

# Real strings, not lorem: the Urdu ones are what the box actually produced.
URDU = "آپ کو چیسٹ میں پین ہے۔ یہ 9 دن سے ہے"
URDU_SHORT = "کچھ نہیں"
HINDI = "आपको छाती में दर्द है। यह 9 दिन से है"
HINGLISH = "chest mein pain hai, 9 din se"
TELUGU = "మీకు ఛాతీలో నొప్పి ఉంది"


# -- the predicate ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        (HINDI, Lang.HI),
        (HINGLISH, Lang.HI),  # romanised Hindi is normal here and must survive
        ("BP 130/80, SpO2 94%, 5 days", Lang.HI),  # Latin + digits inside Indic
        ("बुखार 38°C, TB का इलाज", Lang.HI),  # mixed, and correct
        (HINDI, Lang.MR),  # Marathi shares Devanagari
        (TELUGU, Lang.TE),
        ("", Lang.HI),  # nothing to judge
        (URDU, Lang.EN),  # English is unguarded on purpose — see app.languages
    ],
)
def test_script_safe_text_passes(text: str, lang: Lang) -> None:
    assert is_script_safe(text, lang)
    assert script_problem(text, lang) is None


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        (URDU, Lang.HI, "Arabic"),
        (URDU_SHORT, Lang.HI, "Arabic"),
        (URDU, Lang.MR, "Arabic"),
        (URDU, Lang.TE, "Arabic"),
        (HINDI, Lang.TE, "Devanagari"),  # right script, wrong language
        (TELUGU, Lang.HI, "Telugu"),
        ("आपको " + URDU_SHORT, Lang.HI, "Arabic"),  # one foreign word is enough
    ],
)
def test_wrong_script_is_caught(text: str, lang: Lang, expected: str) -> None:
    assert not is_script_safe(text, lang)
    assert expected in foreign_scripts(text, lang)
    problem = script_problem(text, lang)
    assert problem and expected in problem


def test_the_problem_string_never_leaks_the_text() -> None:
    """A rejected transcript is still a patient's own words, and this string goes
    to the logs."""
    problem = script_problem(URDU, Lang.HI)
    assert problem is not None
    assert URDU not in problem
    assert URDU_SHORT not in problem


# -- the boundary coverage gate -----------------------------------------------

#: Every place a model's words can reach a patient or be stored as theirs. Each
#: entry is (file, the qualified function whose body must run the guard).
_GUARDED_BOUNDARIES: tuple[tuple[str, str], ...] = (
    # Transcription — the patient's own words. Rejected, never rewritten.
    ("app/routes/kiosk.py", "stt"),
    # ...and the doctor's. This one was missed: `app/dictation.py` was exempted
    # below on the reasoning that a consult note is English or Hinglish, and the
    # Alwar box disproved it — a doctor dictating in Hindi got Urdu script back,
    # the same substitution the kiosk had been rejecting since day one. The
    # transcript matters more here than the reasoning allowed for: the mapper
    # reads it to produce a prescription, and `_was_said` checks each drug name
    # against it, so a transcript in the wrong script would have flagged every
    # drug as unsaid. Guarded at the shared helper, which covers `/dictation/stt`
    # and `/notes/stt` in one place.
    ("app/routes/_stt.py", "transcribe_upload"),
    ("app/intake/engine.py", "IntakeEngine._hear"),
    ("app/whatsapp/bot.py", "WhatsAppBot._transcribe"),
    # Generation — replaced by something authored and deterministic.
    ("app/intake/engine.py", "IntakeEngine._turn_v2"),
    ("app/intake/interpret.py", "LLMInterpreter.interpret"),
    ("app/intake/summary.py", "_with_safe_script"),
)


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def _function_source(path: pathlib.Path, qualname: str) -> str:
    """The exact source of one function, found by parsing rather than by slicing.

    A string window over the file was the first version of this and it was wrong
    twice in ten minutes: it matched a Protocol stub instead of the real method,
    and it ran off the end of a long route into the next one. A guard test that
    can pass for the wrong reason is worse than no guard test.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wanted = qualname.split(".")

    def find(nodes: list[ast.stmt], names: list[str]) -> ast.AST | None:
        head, rest = names[0], names[1:]
        for node in nodes:
            if getattr(node, "name", None) != head:
                continue
            if not rest:
                return node
            if isinstance(node, ast.ClassDef):
                return find(node.body, rest)
        return None

    found = find(tree.body, wanted)
    assert found is not None, f"{path.name} no longer defines {qualname!r} — update this list"
    return ast.get_source_segment(path.read_text(encoding="utf-8"), found) or ""


@pytest.mark.parametrize(("path", "qualname"), _GUARDED_BOUNDARIES)
def test_every_model_text_boundary_is_guarded(path: str, qualname: str) -> None:
    """Each known boundary still runs the guard, in its own body.

    Named rather than discovered, because the interesting failure is a *new*
    call site — see the test below, which is the one that catches it.
    """
    body = _function_source(_repo_root() / path, qualname)
    assert "script_problem" in body, f"{path}:{qualname} does not run the script guard"


def test_a_new_model_text_call_site_has_to_be_declared() -> None:
    """The gate that survives the next session.

    Any module that asks an LLM for prose or a recogniser for a transcript must
    either run the guard or be listed here as deliberately exempt. A new one that
    is neither fails this test, which is the only way "not one slip" stays true
    after everybody has forgotten this bug.
    """
    root = _repo_root() / "app"
    # Text that never reaches a patient: the department classifier (returns a
    # key, not prose) and the staff-facing check-in and people helpers.
    #
    # `app/dictation.py` stays here, but on a narrower argument than it used to
    # carry. Its *transcript* is now guarded upstream in `app/routes/_stt.py`,
    # because the premise that a consult note is always English or Hinglish was
    # wrong in the field. What is exempt is the mapping call, whose output is a
    # structured field set rather than prose, and every drug in it is checked
    # against the formulary and against the doctor's own words.
    exempt = {
        "app/dictation.py",
        # Both routes delegate transcription to `app/routes/_stt.py`, which is in
        # the guarded list above. They appear here only because they name the
        # `UsagePurpose` when they call it.
        "app/routes/dictation.py",
        "app/routes/notes.py",
        # The ambient note's mapping call, exempt on the same argument as
        # `app/dictation.py`: its output is a small structured observation for
        # the doctor's own screen, not prose anybody reads back to a patient, and
        # a note cannot prescribe. Its *transcript* is guarded upstream.
        "app/notes.py",
        # Reads the purpose off `usage_events` to divide spend. No model, no text.
        "app/analytics.py",
        "app/routing.py",
        "app/receptionist.py",
        "app/people.py",
        "app/checkins/plan.py",
        "app/checkins/triage.py",
        "app/campaign.py",
    }
    guarded = {path for path, _ in _GUARDED_BOUNDARIES}

    callers: set[str] = set()
    for py in sorted(root.rglob("*.py")):
        if "providers" in py.parts or py.name.startswith("test_"):
            continue  # the provider layer is the transport, not a boundary
        text = py.read_text(encoding="utf-8")
        # A call that returns model prose or a transcript for a patient turn.
        # DICTATION and NOTE joined this list the day a doctor's Hindi came back
        # in Urdu: the original pattern only watched the patient's own turns, so
        # the two staff recorders could be added without this gate noticing.
        if re.search(r"purpose=UsagePurpose\.(INTAKE_TURN|SUMMARY)", text) or re.search(
            r"UsagePurpose\.(DICTATION|NOTE)\b", text
        ):
            callers.add(str(py.relative_to(_repo_root())))

    undeclared = callers - guarded - exempt
    assert not undeclared, (
        "these modules ask a model for patient-facing text and are neither guarded "
        f"nor declared exempt: {sorted(undeclared)}. Run the script guard "
        "(app.languages.script_problem) or add them to `exempt` with a reason."
    )


# -- the boundaries, exercised ------------------------------------------------
#
# The coverage gate above proves the guard is *called*. These prove each caller
# does the right thing with the answer, which is a different question and the one
# that matters to a patient.


async def test_the_kiosk_stt_route_drops_a_wrong_script_transcript(client, monkeypatch) -> None:
    """A transcript this patient cannot read is not a transcript. It comes back
    empty and uncertain, which is what turns on the tap-to-type fallback the
    kiosk always carries (doc 04 law 8)."""
    from app.providers.stt import FakeSTTProvider

    monkeypatch.setattr(
        "app.routes.kiosk.stt_chain", lambda settings=None: [FakeSTTProvider(script=[URDU])]
    )
    resp = await client.post(
        "/kiosk/stt",
        files={"file": ("clip.webm", b"\x00\x01\x02\x03fake-audio", "audio/webm")},
        data={"lang": "hi", "duration_seconds": "2.5"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == ""
    assert body["uncertain"] is True
    # Never transliterated: inventing characters over a clinical complaint is the
    # same failure this system refuses on drug names.
    assert URDU not in resp.text


async def test_the_doctors_stt_route_drops_a_wrong_script_transcript(
    client, session, settings, monkeypatch
) -> None:
    """The one the pilot actually hit, and the one this file originally missed.

    A doctor dictating in Hindi got the recogniser's Urdu back. It matters more
    here than at the kiosk, not less: `dictation_map` reads this text to produce
    a **prescription**, and `_was_said` checks every drug name against it — so a
    transcript in the wrong script would have flagged every drug as unsaid and
    made the doctor acknowledge each one to sign. She gets an empty, uncertain
    reply and the typed path (`POST /dictation/{id}/compose`), which opens the
    same editable fields with no model in the loop.
    """
    from app.auth.tokens import create_access_token
    from app.models.enums import Role
    from app.providers.stt import FakeSTTProvider
    from tests import factories as f

    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    user = f.make_user(hospital, role=Role.DOCTOR)
    session.add(user)
    await session.flush()
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=user.id,
            role=user.role,
            name=user.name,
            settings=settings,
            hospital_id=user.hospital_id,
        ).token
    }

    monkeypatch.setattr(
        "app.routes._stt.stt_chain", lambda settings=None: [FakeSTTProvider(script=[URDU])]
    )
    resp = await client.post(
        "/dictation/stt",
        headers=headers,
        files={"file": ("clip.webm", b"\x00\x01\x02\x03fake-audio", "audio/webm")},
        data={"lang": "hi", "duration_seconds": "4"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == ""
    assert body["uncertain"] is True
    assert URDU not in resp.text

    # The same helper serves the floating dictation, so it is covered too.
    resp = await client.post(
        "/notes/stt",
        headers=headers,
        files={"file": ("clip.webm", b"\x00\x01\x02\x03fake-audio", "audio/webm")},
        data={"lang": "hi"},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == ""


async def test_the_doctors_stt_route_keeps_devanagari_and_hinglish(
    client, session, settings, monkeypatch
) -> None:
    """The check that must not over-fire on the surface where a doctor is
    mid-consult: proper Hindi and romanised Hinglish both pass through."""
    from app.auth.tokens import create_access_token
    from app.models.enums import Role
    from app.providers.stt import FakeSTTProvider
    from tests import factories as f

    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    user = f.make_user(hospital, role=Role.DOCTOR)
    session.add(user)
    await session.flush()
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=user.id,
            role=user.role,
            name=user.name,
            settings=settings,
            hospital_id=user.hospital_id,
        ).token
    }

    for text in (HINDI, HINGLISH):
        monkeypatch.setattr(
            "app.routes._stt.stt_chain", lambda settings=None, t=text: [FakeSTTProvider(script=[t])]
        )
        resp = await client.post(
            "/dictation/stt",
            headers=headers,
            files={"file": ("clip.webm", b"\x00\x01fake", "audio/webm")},
            data={"lang": "hi"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["text"] == text


async def test_the_kiosk_stt_route_keeps_romanised_hinglish(client, monkeypatch) -> None:
    """The check that must not over-fire. Romanised Hindi has no Devanagari in it
    and is completely legitimate here."""
    from app.providers.stt import FakeSTTProvider

    monkeypatch.setattr(
        "app.routes.kiosk.stt_chain", lambda settings=None: [FakeSTTProvider(script=[HINGLISH])]
    )
    resp = await client.post(
        "/kiosk/stt",
        files={"file": ("clip.webm", b"\x00\x01\x02\x03fake-audio", "audio/webm")},
        data={"lang": "hi"},
    )

    assert resp.status_code == 200
    assert resp.json()["text"] == HINGLISH


def test_the_summary_falls_back_to_the_authored_read_back() -> None:
    """The failure the pilot actually hit: a Hindi intake whose read-back came
    back in Urdu. The patient gets the template read-back — authored, in
    Devanagari, and the same one the offline V3 tier speaks."""
    import asyncio
    import json

    from app.intake.state import SessionState
    from app.intake.summary import LLMSummarizer
    from app.models.enums import Channel, IntakeTier
    from app.providers.llm import FakeLLMProvider, FakeLLMScript
    from app.trees import bank
    from app.trees.walker import Walk

    tree = bank.get("dermatology_routing")
    walk = Walk(tree)
    state = SessionState(
        session_id="s-script",
        channel=Channel.KIOSK,
        lang=Lang.HI,
        tree_key=tree.key,
        tree_version=tree.version,
        configured_tier=IntakeTier.RULE_BASED,
        active_tier=IntakeTier.RULE_BASED,
        chief_complaint="घाव ठीक नहीं हो रहा",
    )
    payload = {
        "chief_concern": "Non-healing ulcer",
        "hpi": ["twenty days"],
        "symptoms": [],
        "red_flags": [],
        "history_meds": [],
        "since_last_visit": [],
        "patient_words": {"quote": URDU_SHORT, "lang": "hi", "english": "nothing"},
        "readback": URDU,
        "unclear": [],
    }
    summarizer = LLMSummarizer([FakeLLMProvider(script=[FakeLLMScript(text=json.dumps(payload))])])

    summary = asyncio.run(summarizer.summarize(state, tree, walk))

    assert URDU not in summary.readback
    assert is_script_safe(summary.readback, Lang.HI)
    assert "क्या यह सही है" in summary.readback  # the authored confirm question
    # The quote is dropped rather than replaced: there is nothing honest to put
    # in the place of a patient's own words.
    assert summary.patient_words == {}
    # The doctor's English card is untouched.
    assert summary.chief_concern == "Non-healing ulcer"


def test_a_good_read_back_is_left_exactly_as_written() -> None:
    """The guard must be invisible when the model behaves."""
    import asyncio
    import json

    from app.intake.state import SessionState
    from app.intake.summary import LLMSummarizer
    from app.models.enums import Channel, IntakeTier
    from app.providers.llm import FakeLLMProvider, FakeLLMScript
    from app.trees import bank
    from app.trees.walker import Walk

    tree = bank.get("dermatology_routing")
    good = "आपने बताया: घाव ठीक नहीं हो रहा। क्या यह सही है?"
    payload = {
        "chief_concern": "Non-healing ulcer",
        "hpi": [],
        "symptoms": [],
        "red_flags": [],
        "history_meds": [],
        "since_last_visit": [],
        "patient_words": {"quote": "घाव ठीक नहीं हो रहा", "lang": "hi"},
        "readback": good,
        "unclear": [],
    }
    state = SessionState(
        session_id="s-ok",
        channel=Channel.KIOSK,
        lang=Lang.HI,
        tree_key=tree.key,
        tree_version=tree.version,
        configured_tier=IntakeTier.RULE_BASED,
        active_tier=IntakeTier.RULE_BASED,
    )
    summarizer = LLMSummarizer([FakeLLMProvider(script=[FakeLLMScript(text=json.dumps(payload))])])

    summary = asyncio.run(summarizer.summarize(state, tree, Walk(tree)))

    assert summary.readback == good
    assert summary.patient_words["quote"] == "घाव ठीक नहीं हो रहा"


def test_the_interpreter_drops_a_wrong_script_clarify() -> None:
    """A clarify the patient cannot read is worse than none: dropping it falls
    straight through to the tap options (doc 11 §5)."""
    import asyncio
    import json

    from app.intake.interpret import LLMInterpreter
    from app.providers.llm import FakeLLMProvider, FakeLLMScript
    from app.trees import bank

    tree = bank.get("dermatology_routing")
    node = next(n for n in tree.nodes.values() if n.type.wants_options)
    reply = json.dumps({"clarify": URDU, "confidence": 0.4})
    interpreter = LLMInterpreter([FakeLLMProvider(script=[FakeLLMScript(text=reply)])])

    result = asyncio.run(interpreter.interpret(node, "hmm", Lang.HI))

    assert result.clarify is None
    assert not result.has_value
