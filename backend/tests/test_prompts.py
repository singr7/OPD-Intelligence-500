"""Prompt library and the shared V1/V2 tool contract (doc 02 §2/§5).

Two things are being protected here.

**The loader's strictness.** Prompts are data edited by non-engineers (S18), so
the failure modes are text-shaped: a renamed variable, a placeholder nobody
passes, a version bumped in the filename but not the front matter. Every one of
those produces a *confidently wrong* LLM output rather than a crash, which is the
worst failure a clinical system can have. The loader turns them into errors.

**The tool contract's identity.** `INTAKE_TOOLS` is what makes a mid-session tier
downgrade lossless (doc 02 §5) — V1 and V2 must drive the intake through the same
four functions. Tests here pin the contract itself; `test_providers_vendors.py`
pins that both vendors receive it intact.
"""

from __future__ import annotations

import pytest

from app.prompts.loader import PROMPTS_DIR, Prompt, PromptError, all_prompts, load
from app.prompts.tools import (
    INTAKE_TOOLS,
    TOOL_CONTRACT_VERSION,
    ToolSpec,
    tool,
)

#: The prompts doc 06 requires S3 to ship.
REQUIRED = {"routing", "summarize", "dictation_map", "checkin_personalize"}


# -- the library ---------------------------------------------------------------


def test_the_required_prompts_exist():
    assert {p.id for p in all_prompts()} >= REQUIRED


def test_every_prompt_parses_and_declares_its_variables():
    """The library test: one broken file breaks a live path, and the failure
    would surface as a bad summary rather than an exception."""
    for prompt in all_prompts():
        assert prompt.description, f"{prompt.ref} has no description"
        assert prompt.version >= 1
        assert prompt.template.strip()


def test_prompt_ids_match_their_directory():
    for directory in sorted(p for p in PROMPTS_DIR.iterdir() if p.is_dir()):
        for path in directory.glob("v*.md"):
            assert load(directory.name).id == directory.name, path


def test_load_returns_the_latest_version_unless_pinned(tmp_path):
    root = tmp_path / "greet"
    root.mkdir()
    for version in (1, 2):
        (root / f"v{version}.md").write_text(
            f"---\nid: greet\nversion: {version}\ndescription: d\nvariables: [x]\n---\n{{{{ x }}}}"
        )

    assert load("greet", root=tmp_path).version == 2
    assert load("greet", 1, root=tmp_path).version == 1


def test_missing_version_is_an_error(tmp_path):
    root = tmp_path / "greet"
    root.mkdir()
    (root / "v1.md").write_text("---\nid: greet\nversion: 1\ndescription: d\n---\nhi")

    with pytest.raises(PromptError, match="no v7"):
        load("greet", 7, root=tmp_path)


# -- rendering strictness ------------------------------------------------------


def _prompt(**kwargs) -> Prompt:
    defaults = dict(
        id="t",
        version=1,
        description="d",
        system="",
        template="Hello {{ name }}",
        variables=("name",),
        response_format="text",
    )
    return Prompt(**(defaults | kwargs))


def test_render_fills_placeholders():
    assert _prompt().render(name="Ramesh") == "Hello Ramesh"


def test_render_refuses_a_missing_variable():
    """A prompt shipped with `{{ complaint }}` left literal in it does not crash
    the model — it makes it answer about the literal string. Fail loudly."""
    with pytest.raises(PromptError, match="missing variables"):
        _prompt().render()


def test_render_refuses_an_unexpected_variable():
    """Means the caller and the prompt disagree, and one of them is stale."""
    with pytest.raises(PromptError, match="unexpected variables"):
        _prompt().render(name="x", nonsense="y")


def test_body_using_an_undeclared_variable_is_rejected(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "v1.md").write_text(
        "---\nid: p\nversion: 1\ndescription: d\nvariables: [a]\n---\n{{ a }} {{ b }}"
    )
    with pytest.raises(PromptError, match="undeclared variables"):
        load("p", root=tmp_path)


def test_declaring_a_variable_the_text_stopped_using_is_rejected(tmp_path):
    """The other way prompts rot: an edit drops `{{ since_last_visit }}` from the
    text, every caller keeps passing it, and nobody notices the data stopped
    reaching the model."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "v1.md").write_text(
        "---\nid: p\nversion: 1\ndescription: d\nvariables: [a, unused]\n---\n{{ a }}"
    )
    with pytest.raises(PromptError, match="unused variables"):
        load("p", root=tmp_path)


def test_version_must_agree_with_the_filename(tmp_path):
    """v2.md saying `version: 1` makes `prompt_ref` lie, and a traced-back output
    points at the wrong text."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "v2.md").write_text("---\nid: p\nversion: 1\ndescription: d\n---\nhi")
    with pytest.raises(PromptError, match="filename"):
        load("p", 2, root=tmp_path)


def test_missing_front_matter_is_an_error(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "v1.md").write_text("just some text")
    with pytest.raises(PromptError, match="front matter"):
        load("p", root=tmp_path)


def test_prompt_ref_identifies_the_exact_version():
    """Stamped onto LLM calls so "why did this summary say that?" is answerable
    months later."""
    assert _prompt(id="summarize", version=3).ref == "summarize@v3"


# -- the real prompts render with their real variables -------------------------


def test_routing_prompt_renders():
    prompt = load("routing")
    rendered = prompt.render(
        complaint="chest me gaanth hai",
        lang="hi",
        departments="med_onc: Medical Oncology",
    )
    assert "chest me gaanth hai" in rendered
    assert prompt.response_format == "json"
    assert prompt.system


def test_summarize_prompt_renders_and_carries_the_doc_03_contract():
    """Doc 03 §4's output contract is the prompt's job to enforce; if a rewrite
    drops a required section the doctor's screen silently loses it."""
    prompt = load("summarize")
    rendered = prompt.render(
        lang="hi",
        lang_name="Hindi",
        patient="Ramesh, 54",
        answers="fever x2d",
        red_flags="none",
        history="none",
        since_last_visit="",
    )
    assert "fever x2d" in rendered

    system = prompt.system
    for section in ("chief_concern", "hpi", "symptoms", "red_flags", "patient_words", "readback"):
        assert section in system, f"summary contract lost {section}"
    assert "unclear" in system  # never silently guess (doc 03 §4)
    assert "150 words" in system


def test_dictation_prompt_forbids_drug_substitution():
    """The single most dangerous thing this system could do quietly. Doc 03 §7:
    unknowns flagged, never auto-corrected."""
    system = load("dictation_map").system
    assert "Never substitute" in system
    assert "known" in system


def test_checkin_prompt_renders():
    prompt = load("checkin_personalize")
    rendered = prompt.render(
        protocol="platinum D+2, D+7",
        dictation="cisplatin cycle 2, nausea last time",
        patient="Ramesh, 54",
        reachability="whatsapp, voice",
        today="2026-07-15",
    )
    assert "cisplatin cycle 2" in rendered


# -- the tool contract ---------------------------------------------------------


def test_the_contract_is_the_four_functions_doc_02_names():
    """Doc 02 §5. V1 and V2 both get exactly these; that identity is what makes a
    mid-session tier downgrade lossless."""
    assert [t.name for t in INTAKE_TOOLS] == [
        "get_next_node",
        "save_answer",
        "check_red_flags",
        "finish_and_summarize",
    ]


def test_every_tool_has_a_schema_and_a_behavioural_description():
    for spec in INTAKE_TOOLS:
        assert isinstance(spec, ToolSpec)
        assert spec.parameters["type"] == "object"
        assert spec.required(), f"{spec.name} requires nothing — session_id at least"
        assert len(spec.description) > 40, f"{spec.name}'s description is not instructive"


def test_save_answer_keeps_the_patients_own_words():
    """Doc 03 §4 puts a verbatim quote on the doctor's screen; it has to be
    captured at the point the answer is recorded or it is gone."""
    assert "raw_text" in tool("save_answer").parameters["properties"]


def test_every_tool_is_session_scoped():
    """60-80 concurrent intakes; a tool call without a session id is a race."""
    for spec in INTAKE_TOOLS:
        assert "session_id" in spec.parameters["properties"], spec.name


def test_unknown_tool_lookup_fails_loudly():
    """A model calling something we never declared, or a tier wired with a stale
    contract — both deserve a crash in S5's dispatcher, not a shrug."""
    with pytest.raises(KeyError, match="unknown tool"):
        tool("delete_patient")


def test_contract_is_versioned():
    """A half-finished intake resuming against a redefined `save_answer` is a
    silent data-corruption bug; the version is how S5 detects it."""
    assert TOOL_CONTRACT_VERSION == "1.0"
