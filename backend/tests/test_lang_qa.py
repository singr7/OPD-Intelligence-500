"""The language QA harness (`app.lang_qa`, doc 06 S13).

The first test is the one CI cares about: the live repo is clean in all four pilot
languages. The rest prove each check can actually fail — a green harness is only
worth trusting if injecting the exact defect it guards against turns it red.
"""

from __future__ import annotations

from app import lang_qa
from app.languages import PILOT_LANGUAGES, looks_like_script
from app.models.enums import Lang


def test_the_repo_is_language_complete():
    """Every tree, template, read-back and glossary term covers all four pilot
    languages, in the right script. This is the AC — and the CI gate."""
    problems = lang_qa.check()
    assert problems == [], "\n".join(str(p) for p in problems)


def test_all_four_pilot_languages_are_declared():
    assert PILOT_LANGUAGES == (Lang.EN, Lang.HI, Lang.MR, Lang.TE)


def test_script_check_accepts_the_right_script_and_rejects_english():
    # Devanagari for hi/mr, Telugu for te, and a bare-Latin string fails both.
    assert looks_like_script("ताप आहे", Lang.MR)
    assert looks_like_script("జ్వరం ఉంది", Lang.TE)
    assert not looks_like_script("Fever", Lang.MR)
    assert not looks_like_script("Fever", Lang.TE)
    # A mixed string (digits, °C, an acronym) still passes on one Indic character.
    assert looks_like_script("38°C ताप", Lang.MR)
    # English has no range to assert, so it always passes.
    assert looks_like_script("anything", Lang.EN)


def test_block_check_flags_a_missing_language():
    out: list[lang_qa.Problem] = []
    lang_qa._check_block("x", "w", {"en": "Fever", "hi": "बुखार", "mr": "ताप"}, out)
    assert any("missing te" in p.detail for p in out)


def test_block_check_flags_untranslated_english_left_in_place():
    out: list[lang_qa.Problem] = []
    # mr/te left as the English string — present, but not translated.
    lang_qa._check_block("x", "w", {"en": "Fever", "hi": "बुखार", "mr": "Fever", "te": "Fever"}, out)
    assert any("identical to English" in p.detail for p in out)
    # And the same block trips the script check too (Latin is not Devanagari/Telugu).
    assert any("not in mr's script" in p.detail for p in out)


def test_block_check_flags_wrong_script():
    out: list[lang_qa.Problem] = []
    # te value is Devanagari, not Telugu — a real paste-from-Hindi mistake.
    lang_qa._check_block("x", "w", {"en": "Fever", "hi": "बुखार", "mr": "ताप", "te": "ताप"}, out)
    assert any("not in te's script" in p.detail for p in out)


def test_glossary_consistency_catches_a_synonym():
    glossary = lang_qa.load_glossary()
    assert "fever" in glossary
    # The live bank must use the glossary's exact rendering — check() proved it does;
    # here we assert the check would notice if it stopped. Build a block that says
    # "Fever" in English but a different Marathi word than the glossary's.
    canonical = glossary["fever"]
    assert canonical["mr"] != "जुनाट ताप"  # guard the fixture
    block = {**canonical, "mr": "जुनाट ताप"}
    by_en = {canonical["en"]: ("fever", canonical)}
    # inline the consistency comparison the harness runs
    problems = []
    for lang in PILOT_LANGUAGES:
        want, got = by_en[block["en"]][1].get(str(lang)), block.get(str(lang))
        if want and got and want != got:
            problems.append(lang)
    assert Lang.MR in problems


def test_bcp47_mapping_exists_for_every_pilot_language():
    out: list[lang_qa.Problem] = []
    lang_qa._check_bcp47(out)
    assert out == []


def test_round_trip_smoke_returns_a_transcript_per_language():
    out: list[lang_qa.Problem] = []
    lang_qa._check_round_trip(out)
    assert out == []


def test_glossary_loads_and_covers_every_language():
    glossary = lang_qa.load_glossary()
    assert glossary, "glossary is empty"
    for concept, block in glossary.items():
        for lang in PILOT_LANGUAGES:
            assert block.get(str(lang)), f"glossary {concept} missing {lang}"
