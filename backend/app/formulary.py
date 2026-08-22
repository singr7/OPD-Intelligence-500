"""The drug formulary, and the one rule it exists to enforce (doc 03 §7).

> "meds[] ... validated against a formulary list w/ fuzzy match; unknowns
> flagged, **never auto-corrected**" — doc 03 §7

`seeds/formulary.json` is 189 generics and their Indian brand names — what a
doctor in Alwar actually dictates ("Tab Augmentin 625 BD", "Inj Kemocarb",
"Zoladex next month"). This module turns a dictated string into a verdict about
that string. It never turns it into a different string.

## Why `known` is exact-only and fuzzy is advisory

The dangerous failure in this whole session is a **silent substitution**: the
model (or us) hears "vinblastine", decides the nearest formulary entry is
"vincristine", and writes that into a prescription. Those are different drugs
with different doses and different ways of killing someone, and the difference
is invisible to a doctor scanning a diff — the field looks *right*, so it does
not get read.

So the two jobs are split by construction:

* **`known`** is set **only by an exact match** on the normalised name. There is
  no score, no threshold, no "close enough". A name that is not in the book comes
  back `known=False` carrying exactly the characters that were dictated.
* **`suggestions`** are fuzzy neighbours, offered to the *doctor* in the review
  UI as "did you mean". They are advice on a screen, never a value in a field.

That split is what makes the S10 acceptance criterion ("zero silent drug
substitutions") a property of the code rather than a thing we tested for once:
there is no code path from a fuzzy score to a written name.

## Ambiguity is worse than being unknown

If a dictated name is fuzzily close to **two different generics**, that is the
look-alike case above, and it is the one where a helpful UI does the most damage
by nominating a winner. `ambiguous` says so, and the console shows the
neighbours side by side without a default.

## Normalisation

Doctors dictate a form, a name and a strength in one breath. `normalise` strips
the form word (`Tab`, `Inj`, `Syp`, …) and the strength tokens (`625`, `500mg`,
`40 mg`) and lowercases the rest — applied identically to the book and to the
query, so "Tab. Augmentin 625" and "augmentin" are the same key. It does **not**
touch brand suffixes: `Orofer XT` and `Neurobion Forte` are real, distinct
products, and dropping the suffix would silently merge them with their siblings.

## Scope: one book, two shelves (doc 24 §6.3)

A department's system of medicine decides which entries this module may call
known — `care_system.capabilities_for(...).formulary_scope`. The rule is
symmetric and runs in both directions: an ayurvedic preparation dictated in an
ayurveda consult must not come back "not in formulary", and a cytotoxic must not
become dictatable in one. The first direction is the one that would annoy a
doctor into ignoring the flag; the second is the one that would hurt a patient.

Scope filters **which shelf is searched**, and nothing else. It does not touch
normalisation, it does not lower the exact-match bar, and it does not turn a
fuzzy neighbour into a written name — a drug off the wrong shelf is simply not
found, which is the same `known=False` verdict, carrying the same verbatim
characters, that an invented name gets. There is deliberately no "search the
other shelf and warn": that path ends in a console offering a cytotoxic as a
did-you-mean during an ayurveda consult.

An entry with no `scope` in the seed file is allopathy — the same reading
`care_system.care_system_of(None)` takes of a department that predates doc 24.
The 189 oncology generics were authored before scopes existed and genuinely are
allopathic, so they are not re-tagged by hand.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from typing import Any

from app.models.enums import CareSystem

SEEDS_DIR = Path(__file__).resolve().parents[2] / "seeds"
FORMULARY_FILE = SEEDS_DIR / "formulary.json"

#: A fuzzy neighbour is worth showing the doctor above this. Tuned on the
#: look-alike pairs in `test_formulary.py` (vincristine/vinblastine sit at ~0.79,
#: cisplatin/carboplatin at ~0.84) — high enough that a suggestion list stays
#: short, and irrelevant either way to what gets written, since suggestions never
#: become values.
SUGGEST_THRESHOLD = 0.82

#: At most this many neighbours: a "did you mean" list longer than three is a
#: research task, and the doctor's answer is then to retype the name.
MAX_SUGGESTIONS = 3

#: Dosage forms as dictated, in every spelling heard on the ward. Stripped from
#: both ends of a name — "Tab Dolo" and "Dolo tablet" are the same drug.
_FORM_WORDS = {
    "tab",
    "tabs",
    "tablet",
    "tablets",
    "cap",
    "caps",
    "capsule",
    "capsules",
    "inj",
    "injection",
    "amp",
    "ampoule",
    "vial",
    "syp",
    "syr",
    "syrup",
    "susp",
    "suspension",
    "sol",
    "solution",
    "drop",
    "drops",
    "oint",
    "ointment",
    "cream",
    "gel",
    "lotion",
    "patch",
    "spray",
    "gargle",
    "mouthwash",
    "paint",
    "powder",
    "sachet",
    "infusion",
    "iv",
    "im",
    "sc",
    "po",
}

#: "625", "500mg", "40 mg", "1.5g", "5000 iu", "0.9%" — a strength, not a name.
_STRENGTH = re.compile(
    r"^\d+(?:[.,]\d+)?\s*(?:mg|mcg|ug|g|gm|gms|ml|l|iu|u|units?|%|mg/ml|mg/m2)?$",
    re.IGNORECASE,
)

#: A unit that got separated from its number ("500 mg" tokenises to `500`, `mg`).
#: Dropped too, so a string of pure dosage normalises to "" — "no name heard" —
#: rather than to the word "mg", which would then go hunting for neighbours.
_UNITS = {"mg", "mcg", "ug", "g", "gm", "gms", "ml", "l", "iu", "u", "unit", "units", "%"}

_PUNCT = re.compile(r"[^\w%/+-]+")


def normalise(name: str) -> str:
    """A dictated drug name reduced to its comparison key.

    Lowercase, punctuation to spaces, form words and strength tokens dropped.
    Returns "" for a string that was nothing but a form and a number — the caller
    treats that as "no drug name heard" rather than as an unknown drug.
    """
    lowered = _PUNCT.sub(" ", name.lower()).strip()
    tokens = [t for t in lowered.split() if t]
    kept = [
        t for t in tokens if t not in _FORM_WORDS and t not in _UNITS and not _STRENGTH.match(t)
    ]
    return " ".join(kept)


#: The shelf an entry with no `scope` sits on. Not a fallback for a *wrong*
#: scope — `_parse` raises on one of those, for the reason `CareSystemError`
#: exists — but the reading of an entry authored before scopes did.
DEFAULT_SCOPE = "allopathy"


@dataclass(frozen=True, slots=True)
class Drug:
    """One generic and the brands it is dictated as."""

    generic: str
    drug_class: str
    forms: tuple[str, ...]
    brands: tuple[str, ...]
    #: Which system of medicine may call this entry known — matched against a
    #: department's `capabilities.formulary_scope`. Not a display field and not a
    #: clinical claim about the drug; purely which shelf it is filed on.
    scope: str = DEFAULT_SCOPE

    @property
    def names(self) -> tuple[str, ...]:
        return (self.generic, *self.brands)


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A fuzzy neighbour, for the doctor's eyes only. Never a written value."""

    name: str
    generic: str
    score: float


@dataclass(frozen=True, slots=True)
class Lookup:
    """The verdict on one dictated name. `query` is preserved verbatim, always."""

    query: str
    normalized: str
    known: bool
    matched: str | None = None
    generic: str | None = None
    drug_class: str | None = None
    suggestions: tuple[Suggestion, ...] = ()
    #: Fuzzily close to more than one generic — the look-alike case. Never
    #: resolved for the doctor; the console shows the candidates without a default.
    ambiguous: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "known": self.known,
            "matched": self.matched,
            "generic": self.generic,
            "drug_class": self.drug_class,
            "ambiguous": self.ambiguous,
            "suggestions": [
                {"name": s.name, "generic": s.generic, "score": round(s.score, 3)}
                for s in self.suggestions
            ],
        }


@dataclass(slots=True)
class Formulary:
    """One shelf of the book, loaded. Immutable in practice; rebuilt by reloading.

    A `Formulary` is **one system of medicine's formulary**, not the whole file.
    The file holds every shelf and `get_formulary(scope=...)` hands back the one
    a consult is entitled to, so the index, the fuzzy neighbour search, the
    prompt hint and `names` are all scoped by construction rather than by
    remembering to filter. That is the point: the neighbour search walks the
    whole index, and a shared index filtered afterwards would score a dictated
    ayurvedic name against 189 cytotoxics and then drop the winners — the right
    answer by luck, and one refactor away from offering them as did-you-mean.
    """

    version: int
    drugs: tuple[Drug, ...]
    #: normalised name -> (name as written in the book, its drug)
    _index: dict[str, tuple[str, Drug]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self._index:
            for drug in self.drugs:
                for name in drug.names:
                    key = normalise(name)
                    # First writer wins: a brand that collides with another
                    # product's normalised key keeps the earlier entry rather than
                    # silently re-pointing it at a different generic.
                    self._index.setdefault(key, (name, drug))

    @property
    def names(self) -> tuple[str, ...]:
        """Every dictatable name in the book, generics and brands, in file order.

        This shelf's names, because a `Formulary` is one shelf — see the class
        docstring. There is deliberately no all-shelves variant: a flat list
        spanning two systems of medicine is not a thing any clinical path should
        be reading.
        """
        return tuple(name for drug in self.drugs for name in drug.names)

    def lookup(self, name: str) -> Lookup:
        """Is this dictated name on this shelf? Never rewrites `name`."""
        key = normalise(name)
        if not key:
            return Lookup(query=name, normalized=key, known=False)

        if hit := self._index.get(key):
            matched, drug = hit
            return Lookup(
                query=name,
                normalized=key,
                known=True,
                matched=matched,
                generic=drug.generic,
                drug_class=drug.drug_class,
            )

        suggestions = self._neighbours(key)
        return Lookup(
            query=name,
            normalized=key,
            known=False,
            suggestions=suggestions,
            ambiguous=len({s.generic for s in suggestions}) > 1,
        )

    def _neighbours(self, key: str) -> tuple[Suggestion, ...]:
        scored: list[Suggestion] = []
        for indexed, (name, drug) in self._index.items():
            score = SequenceMatcher(None, key, indexed).ratio()
            if score >= SUGGEST_THRESHOLD:
                scored.append(Suggestion(name=name, generic=drug.generic, score=score))
        # Deterministic: score desc, then name — two neighbours can tie exactly,
        # and a suggestion list that reorders between calls looks like the system
        # changed its mind about a drug.
        scored.sort(key=lambda s: (-s.score, s.name))
        return tuple(scored[:MAX_SUGGESTIONS])

    def prompt_hint(self) -> str:
        """This shelf as the mapping prompt sees it — one line per generic.

        Handed to the model for the `known` flag only; the prompt says so in
        capitals and this module overrides whatever it claims anyway. Ordering is
        file order, so the rendered prompt (and therefore the prompt cache) is
        stable across processes.

        One shelf and not the file, for a reason beyond tidiness: the hint is the
        list of names the model is told exist, so an ayurveda consult whose hint
        carried 189 cytotoxics would be a transcript of "shatavari" sitting next
        to a prompt full of plausible-looking oncology names. `validate_meds` would still
        throw the model's verdict away, but the *name it echoes back* is the one
        thing this system takes from the model verbatim.
        """
        return "\n".join(
            f"{drug.generic} [{drug.drug_class}]: {', '.join(drug.brands)}"
            if drug.brands
            else f"{drug.generic} [{drug.drug_class}]"
            for drug in self.drugs
        )


class FormularyError(ValueError):
    """A malformed entry. Never a silent repair.

    Same stance as `CareSystemError`: a typo in a `scope` must not file a drug on
    a shelf nobody searches (where it reads as "not in formulary" forever, and
    looks like the doctor mis-dictated) or on the wrong one (where it becomes
    dictatable in the wrong consult). Both are invisible on every screen.
    """


def _scope_of(row: dict[str, Any], known: frozenset[str]) -> str:
    raw = row.get("scope")
    if raw is None:
        return DEFAULT_SCOPE
    scope = str(raw)
    if scope not in known:
        raise FormularyError(
            f"{row.get('generic', '?')!r}: unknown formulary scope {scope!r}; "
            f"expected one of {sorted(known)}"
        )
    return scope


def _parse(payload: dict[str, Any], scope: str = DEFAULT_SCOPE) -> Formulary:
    """One shelf of the file, as a `Formulary`.

    **Every** row is validated, not only the ones kept: a typo in an ayurveda
    row must fail when the oncology book is loaded too, or it stays invisible
    until the morning somebody opens the ayurveda OPD.
    """
    # The shelves are exactly the systems of medicine the platform has. Derived
    # from the enum rather than listed here, so a third system cannot be added to
    # `CareSystem` and leave this file quietly rejecting its formulary.
    known = frozenset(member.value for member in CareSystem)
    rows = [(row, _scope_of(row, known)) for row in payload.get("drugs", ())]
    drugs = tuple(
        Drug(
            generic=str(row["generic"]),
            drug_class=str(row.get("class", "other")),
            forms=tuple(str(f) for f in row.get("forms", ())),
            brands=tuple(str(b) for b in row.get("brands", ())),
            scope=row_scope,
        )
        for row, row_scope in rows
        if row_scope == scope
    )
    return Formulary(version=int(payload.get("version", 1)), drugs=drugs)


@cache
def get_formulary(path: Path | None = None, scope: str = DEFAULT_SCOPE) -> Formulary:
    """One system of medicine's formulary. Cached per shelf — the file is data
    that changes at deploy time.

    `scope` defaults to allopathy so a caller with no department in hand — a
    script, a test written before doc 24 — gets exactly today's book. The live
    paths never take the default; they pass the scope their department's
    capabilities derived.
    """
    return _parse(json.loads((path or FORMULARY_FILE).read_text(encoding="utf-8")), scope)


def lookup(name: str, *, scope: str = DEFAULT_SCOPE) -> Lookup:
    """Convenience for the common case: look one name up on one shelf."""
    return get_formulary(scope=scope).lookup(name)
