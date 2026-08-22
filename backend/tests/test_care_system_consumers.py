"""What the capability flags actually *do* (doc 24 §6.3, §6.4) — SESSION-AYUR-3.

AYUR-0 derived eight flags and delivered them on three payloads; nothing read
them. This file pins the three that stopped being decorative: `formulary_scope`,
`prompt_pack`, and the rule that both are derived from a department row rather
than taken from whoever is asking.

The property under all of it is doc 24 §2's: a system of medicine changes what a
consult *does* only through a flag on the capabilities row. So these tests assert
on behaviour visible to a doctor — a drug flagged or not flagged, a prompt with
one register or another — never on the enum.
"""

from __future__ import annotations

import json

import pytest

from app import formulary as formulary_mod
from app.care_system import capabilities_for
from app.dictation import DictationMapping, MedLine, validate_meds
from app.prompts import BASE_PACK, load_packed, packed_id
from app.prompts.loader import PROMPTS_DIR

# -- the two shelves ----------------------------------------------------------

#: One preparation off each shelf, named the way a doctor dictates it. Chosen so
#: neither is fuzzily close to anything on the other shelf — the point here is
#: the exact-match verdict, not the suggestion machinery.
AYURVEDIC = "Avipattikar Churna"
ALLOPATHIC = "Inj Kemocarb"


@pytest.mark.parametrize(
    ("name", "scope", "known"),
    [
        (AYURVEDIC, "ayurveda", True),
        (AYURVEDIC, "allopathy", False),
        (ALLOPATHIC, "allopathy", True),
        (ALLOPATHIC, "ayurveda", False),
    ],
)
def test_a_preparation_is_known_only_on_its_own_shelf(name: str, scope: str, known: bool) -> None:
    """doc 24 §8's named acceptance: known in AYUR, flagged in MEDONC.

    Both directions are asserted because both are failures, in different ways.
    An ayurvedic churna flagged "not in formulary" during an ayurveda consult is
    the flag becoming noise, which is how a doctor learns to clear flags without
    reading them. A cytotoxic silently *known* in an ayurveda consult is the
    other thing entirely.
    """
    assert formulary_mod.lookup(name, scope=scope).known is known


def test_the_wrong_shelf_offers_no_suggestions_either() -> None:
    """Not found is the whole verdict — there is no "did you mean" across shelves.

    The suggestion list is the one place a wrong name is shown next to a real
    one, and a console offering carboplatin as a did-you-mean during an ayurveda
    consult is the failure this module's `known`/`suggestions` split exists to
    make impossible. `_neighbours` searches one shelf's index, so this holds by
    construction rather than by filtering afterwards.
    """
    verdict = formulary_mod.lookup("Kemocarb", scope="ayurveda")
    assert verdict.known is False
    assert verdict.suggestions == ()


def test_a_dictated_name_is_never_rewritten_on_either_shelf() -> None:
    """The S10 invariant, restated for the second shelf.

    Scope decides which entries count as known. It must not become a second way
    for a name to change on its way to a prescription.
    """
    heard = "Avipattikar Choorna 3 gm"
    for scope in ("allopathy", "ayurveda"):
        assert formulary_mod.lookup(heard, scope=scope).query == heard


def test_the_prompt_hint_carries_one_shelf_only() -> None:
    """The hint is the list of names the model is told exist.

    `validate_meds` throws the model's verdict away, but the name it echoes back
    is taken verbatim — so an ayurveda consult must not be mapped against a
    prompt listing 189 cytotoxics.
    """
    ayurveda = formulary_mod.get_formulary(scope="ayurveda").prompt_hint()
    allopathy = formulary_mod.get_formulary(scope="allopathy").prompt_hint()

    assert "avipattikar churna" in ayurveda
    assert "carboplatin" not in ayurveda
    assert "carboplatin" in allopathy
    assert "avipattikar churna" not in allopathy


def test_an_unknown_scope_raises_rather_than_being_filed_somewhere() -> None:
    """A typo in a seed row must not put a drug on a shelf nobody searches.

    Filed there it reads as "not in formulary" forever, which on screen looks
    exactly like a doctor mis-dictating a name.
    """
    with pytest.raises(formulary_mod.FormularyError):
        formulary_mod._parse({"drugs": [{"generic": "x", "scope": "ayurved"}]})


def test_a_bad_scope_fails_even_when_another_shelf_is_being_loaded() -> None:
    """Or it stays invisible until the morning somebody opens the ayurveda OPD."""
    payload = {"drugs": [{"generic": "cisplatin"}, {"generic": "x", "scope": "ayurved"}]}
    with pytest.raises(formulary_mod.FormularyError):
        formulary_mod._parse(payload, "allopathy")


def test_an_entry_with_no_scope_is_allopathy() -> None:
    """The 189 oncology generics predate scopes and were not re-tagged by hand."""
    payload = {"drugs": [{"generic": "cisplatin"}]}
    assert formulary_mod._parse(payload, "allopathy").lookup("cisplatin").known is True
    assert formulary_mod._parse(payload, "ayurveda").lookup("cisplatin").known is False


# -- validate_meds takes the scope, and defaults to today ---------------------


def _mapping(name: str) -> DictationMapping:
    return DictationMapping(meds=(MedLine(name=name, as_spoken=name),))


def test_validate_meds_flags_by_the_scope_it_is_given() -> None:
    ayur = validate_meds(_mapping(AYURVEDIC), transcript=AYURVEDIC, scope="ayurveda")
    onco = validate_meds(_mapping(AYURVEDIC), transcript=AYURVEDIC, scope="allopathy")

    assert ayur.meds[0].known is True
    assert onco.meds[0].known is False
    # The name survives both verdicts unchanged — the S10 invariant again, this
    # time through the caller a doctor's dictation actually goes through.
    assert ayur.meds[0].name == onco.meds[0].name == AYURVEDIC


def test_validate_meds_without_a_scope_is_todays_behaviour() -> None:
    """Every caller written before doc 24 keeps its verdicts, unedited."""
    before = validate_meds(_mapping(ALLOPATHIC), transcript=ALLOPATHIC)
    after = validate_meds(_mapping(ALLOPATHIC), transcript=ALLOPATHIC, scope="allopathy")
    assert before.meds[0].known is after.meds[0].known is True


# -- the prompt packs ---------------------------------------------------------

#: The three doc 24 §6.4 names: the prompts whose *wording* is care-system
#: specific. Everything else in `backend/prompts/` is about the task.
PACKED = ("summarize", "dictation_map", "research_assist")


@pytest.mark.parametrize("prompt_id", PACKED)
def test_every_system_of_medicine_has_these_three_prompts(prompt_id: str) -> None:
    """The fallback in `packed_id` must never be the reason an ayurveda consult
    is summarised in oncology language.

    `load_packed` falls back to the base prompt when a pack has no variant, which
    is right for `routing` and `mrd_extract` and wrong for these three. This test
    is what keeps that distinction deliberate: add a system of medicine and it
    fails until the three prompts it needs exist.
    """
    for caps in (capabilities_for("allopathy"), capabilities_for("ayurveda")):
        if caps.prompt_pack == BASE_PACK:
            continue
        assert packed_id(prompt_id, caps.prompt_pack) != prompt_id, (
            f"the {caps.prompt_pack!r} pack has no {prompt_id!r} of its own"
        )


def test_a_care_system_agnostic_prompt_is_shared_not_forked() -> None:
    """`routing` classifies a chief complaint into a department; that job does not
    change with the system of medicine, and four copies of it would drift."""
    assert packed_id("routing", "ayurveda") == "routing"


@pytest.mark.parametrize("prompt_id", PACKED)
def test_a_packed_prompt_declares_the_same_variables(prompt_id: str) -> None:
    """A pack changes the register, never the contract.

    The caller renders one set of variables and parses one response shape; a pack
    that quietly dropped a variable would render a prompt with `{{ answers }}`
    left literal in it, which produces a confident answer rather than a crash.
    """
    base = load_packed(prompt_id, BASE_PACK)
    ayurveda = load_packed(prompt_id, "ayurveda")
    assert set(ayurveda.variables) == set(base.variables)
    assert ayurveda.response_format == base.response_format


@pytest.mark.parametrize("prompt_id", PACKED)
def test_a_packed_prompt_is_traceable_to_its_own_text(prompt_id: str) -> None:
    """`Prompt.ref` is stamped onto the LLM call, so "which text produced this?"
    stays answerable — the reason prompts are versioned at all."""
    assert load_packed(prompt_id, "ayurveda").ref.startswith(f"{prompt_id}_ayurveda@v")


def test_the_ayurveda_research_prompt_keeps_every_refusal() -> None:
    """Framing is all a pack may change (doc 24 §6.4).

    The assistant's refusals — doses, urgency, a diagnosis this patient was not
    given, certainty it does not have — are properties of the tool, not of a
    system of medicine. A pack that softened one would be a different tool
    wearing the same name.
    """
    system = load_packed("research_assist", "ayurveda").system.lower()
    for phrase in ("dose", "urgency", "local protocol", "cutoff"):
        assert phrase in system, f"the ayurveda research prompt must address {phrase!r}"


def test_the_ayurveda_summary_prompt_refuses_to_name_a_constitution() -> None:
    """doc 24 §4: dosha language is flavour, never triage — and never inferred.

    The patient was not asked to know or pick a prakriti and the intake does not
    contain one. A summary that opens with "likely pitta prakriti" has decided
    the consult before the doctor has looked at the patient.
    """
    system = load_packed("summarize", "ayurveda").system.lower()
    assert "never name a prakriti" in system


@pytest.mark.parametrize("prompt_id", PACKED)
def test_every_ayurveda_prompt_says_it_is_unreviewed(prompt_id: str) -> None:
    """doc 24 §9, the oncology tree bank's stance: model-drafted content is not
    patient-ready because the tests pass. The flag lives in the file, where the
    BAMS reviewer will be reading."""
    text = (PROMPTS_DIR / f"{prompt_id}_ayurveda" / "v1.md").read_text(encoding="utf-8")
    assert "UNREVIEWED" in text


def test_the_fake_provider_can_demo_an_ayurveda_consult() -> None:
    """The MRD precedent (doc 24 §3.3): every flow demoable on `LLM_PROVIDER=fake`.

    Keyed by the packed prompt id, which is why packs work here without the fake
    knowing packs exist. The fixture must be *usable*: its preparations are on
    the ayurveda shelf, so the demo shows a real verdict rather than a screen of
    flags.
    """
    from app.providers.llm import _CANNED_JSON

    canned = _CANNED_JSON.get("dictation_map_ayurveda")
    assert canned is not None

    mapping = validate_meds(DictationMapping.parse(json.loads(canned)), scope="ayurveda")
    verdicts = {med.name: med.known for med in mapping.meds}
    assert verdicts["Avipattikar Churna"] is True
    # And one deliberately off-shelf, so the demo shows the flag too.
    assert any(known is False for known in verdicts.values())


def test_the_ayurveda_demo_orders_no_treatment_cycles() -> None:
    """`shows_regimen_events` is false for ayurveda, so a fixture carrying cycle
    lines would be demonstrating a surface the capability flags switch off."""
    from app.providers.llm import _CANNED_JSON

    assert json.loads(_CANNED_JSON["dictation_map_ayurveda"])["treatment_events"] == []
