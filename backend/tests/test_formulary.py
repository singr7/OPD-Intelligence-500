"""The formulary's one job: never rename a drug (doc 03 §7).

These tests are the S10 acceptance criterion at its narrowest point. Everything
downstream — the mapper, the review UI, the signed prescription — carries the
`name` this module was handed, so if nothing here can rewrite a name, nothing
there can either.
"""

from __future__ import annotations

import pytest

from app.formulary import (
    SUGGEST_THRESHOLD,
    Formulary,
    get_formulary,
    lookup,
    normalise,
)


@pytest.fixture(scope="module")
def book() -> Formulary:
    return get_formulary()


# -- normalisation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("dictated", "expected"),
    [
        ("Tab Augmentin 625", "augmentin"),
        ("Tab. Augmentin 625 mg", "augmentin"),
        ("INJ KEMOCARB", "kemocarb"),
        ("Syp Duphalac 15ml", "duphalac"),
        ("Zoladex injection", "zoladex"),
        ("cap. Temoside 250 mg", "temoside"),
        ("  Dolo   650  ", "dolo"),
        # Brand suffixes are part of the product, not noise: Orofer and Orofer XT
        # are different things on a shelf, and merging them is a substitution.
        ("Orofer XT", "orofer xt"),
        ("Neurobion Forte", "neurobion forte"),
        # Nothing but a form and a dose is not a drug name at all.
        ("500 mg", ""),
        ("Tab 500mg", ""),
        ("inj", ""),
    ],
)
def test_normalise_strips_form_and_strength_but_not_the_name(dictated: str, expected: str) -> None:
    assert normalise(dictated) == expected


def test_a_dose_only_string_is_not_an_unknown_drug(book: Formulary) -> None:
    """Empty key ⇒ no name heard, and no neighbour hunt on the word "mg"."""
    result = book.lookup("500 mg")
    assert result.normalized == ""
    assert result.known is False
    assert result.suggestions == ()


# -- known: exact only --------------------------------------------------------


@pytest.mark.parametrize(
    "dictated",
    [
        "Augmentin",
        "Tab Augmentin 625",
        "carboplatin",
        "Kemocarb",
        "Herceptin",
        "inj Zometa 4mg",
        "Tab Dolo 650",
        "morphine",
        "Morcontin",
        "paclitaxel",
    ],
)
def test_names_in_the_book_are_known(book: Formulary, dictated: str) -> None:
    result = book.lookup(dictated)
    assert result.known is True
    assert result.generic


def test_a_known_lookup_reports_the_generic_behind_the_brand(book: Formulary) -> None:
    result = book.lookup("Inj Kemocarb 450")
    assert result.known is True
    assert result.matched == "Kemocarb"
    assert result.generic == "carboplatin"
    assert result.drug_class == "cytotoxic-platinum"


@pytest.mark.parametrize(
    "dictated",
    [
        "Zolgensma",  # a real drug, not on this formulary
        "paclitaxal",  # one letter off a real one
        "ondansetran",  # ditto
        "Vinblastin",  # ditto, and the dangerous family
        "Tab Fantasyzole 10",  # not a drug at all
    ],
)
def test_a_name_not_in_the_book_is_never_known(book: Formulary, dictated: str) -> None:
    result = book.lookup(dictated)
    assert result.known is False


# -- the core invariant: no path from a score to a name -----------------------


def test_a_near_miss_keeps_the_dictated_spelling(book: Formulary) -> None:
    """0.95 similar to `vinblastine` — and still comes back as what was said.

    This is the whole session in one assertion. A helpful system writes
    "vinblastine" here; a safe one hands the doctor their own word back with a
    flag on it.
    """
    result = book.lookup("Vinblastin")
    assert result.query == "Vinblastin"
    assert result.known is False
    assert result.matched is None
    assert result.generic is None
    assert [s.name for s in result.suggestions] == ["vinblastine"]
    assert result.suggestions[0].score > 0.9


def test_every_lookup_preserves_its_query_verbatim(book: Formulary) -> None:
    for dictated in ("Vinblastin", "paclitaxal", "  Tab Augmentin 625 ", "Zolgensma"):
        assert book.lookup(dictated).query == dictated


def test_suggestions_never_appear_on_a_known_drug(book: Formulary) -> None:
    """Nothing to disambiguate when the name is in the book — and no invitation
    to "improve" a name that is already right."""
    assert book.lookup("carboplatin").suggestions == ()


def test_suggestions_are_ranked_and_capped(book: Formulary) -> None:
    result = book.lookup("carbplatin")
    scores = [s.score for s in result.suggestions]
    assert scores == sorted(scores, reverse=True)
    assert len(result.suggestions) <= 3
    assert all(s.score >= SUGGEST_THRESHOLD for s in result.suggestions)


def test_suggestions_are_deterministic(book: Formulary) -> None:
    first = book.lookup("Celkeran"[1:])
    second = book.lookup("Celkeran"[1:])
    assert [(s.name, s.score) for s in first.suggestions] == [
        (s.name, s.score) for s in second.suggestions
    ]


# -- ambiguity: the look-alike case -------------------------------------------


def test_two_different_generics_within_reach_is_flagged_ambiguous(book: Formulary) -> None:
    """`Cytolatin` sits between Cytoplatin (cisplatin) and Cytoblastin
    (vinblastine) — a platinum and a vinca alkaloid. Picking one for the doctor
    here is how a patient gets the wrong drug class."""
    result = book.lookup("Cytolatin")
    assert result.known is False
    assert result.ambiguous is True
    generics = {s.generic for s in result.suggestions}
    assert {"cisplatin", "vinblastine"} <= generics


def test_neighbours_of_a_single_generic_are_not_ambiguous(book: Formulary) -> None:
    """`carbplatin` is close to both `carboplatin` and its brand `Carboplat` —
    same drug twice, so there is nothing for the doctor to choose between."""
    result = book.lookup("carbplatin")
    assert result.suggestions
    assert {s.generic for s in result.suggestions} == {"carboplatin"}
    assert result.ambiguous is False


def test_the_alkeran_leukeran_family_is_ambiguous(book: Formulary) -> None:
    """Melphalan and chlorambucil, one letter apart on an Indian shelf."""
    result = book.lookup("Lukeran")
    assert result.ambiguous is True
    assert {s.generic for s in result.suggestions} == {"chlorambucil", "melphalan"}


# -- the book itself ----------------------------------------------------------


def test_the_seeded_formulary_covers_the_clinic(book: Formulary) -> None:
    """doc 06 S10 asks for ~300 dictatable drug names; the file carries the
    generics *and* the brands, because a doctor dictates the brand."""
    assert len(book.drugs) >= 150
    assert len(book.names) >= 300


def test_the_book_spans_more_than_chemotherapy(book: Formulary) -> None:
    """An oncology OPD prescription is mostly supportive care — antiemetics,
    G-CSF, opioids, PPIs. A chemo-only formulary would flag half a real
    prescription as unknown and train the doctor to ignore the flag."""
    classes = {drug.drug_class for drug in book.drugs}
    for required in (
        "cytotoxic-platinum",
        "targeted-tki",
        "immunotherapy",
        "hormonal",
        "supportive-antiemetic",
        "supportive-gcsf",
        "analgesic-opioid",
        "antibiotic",
        "steroid",
        "gastro",
    ):
        assert required in classes


def test_every_name_in_the_book_looks_itself_up(book: Formulary) -> None:
    """No entry is shadowed by another's normalised key."""
    for drug in book.drugs:
        for name in drug.names:
            result = book.lookup(name)
            assert result.known is True, name


def test_the_prompt_hint_is_stable_and_mentions_brands(book: Formulary) -> None:
    hint = book.prompt_hint()
    assert hint == book.prompt_hint()
    assert "carboplatin [cytotoxic-platinum]: Carboplat, Kemocarb, Oncocarbin" in hint


def test_module_level_lookup_uses_the_default_book() -> None:
    assert lookup("Tab Augmentin 625").known is True
