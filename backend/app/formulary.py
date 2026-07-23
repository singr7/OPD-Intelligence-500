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
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from typing import Any

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
    "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules",
    "inj", "injection", "amp", "ampoule", "vial", "syp", "syr", "syrup",
    "susp", "suspension", "sol", "solution", "drop", "drops", "oint", "ointment",
    "cream", "gel", "lotion", "patch", "spray", "gargle", "mouthwash", "paint",
    "powder", "sachet", "infusion", "iv", "im", "sc", "po",
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
        t
        for t in tokens
        if t not in _FORM_WORDS and t not in _UNITS and not _STRENGTH.match(t)
    ]
    return " ".join(kept)


@dataclass(frozen=True, slots=True)
class Drug:
    """One generic and the brands it is dictated as."""

    generic: str
    drug_class: str
    forms: tuple[str, ...]
    brands: tuple[str, ...]

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
    """The loaded book. Immutable in practice; rebuilt only by reloading the file."""

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
        """Every dictatable name, generics and brands, in file order."""
        return tuple(name for drug in self.drugs for name in drug.names)

    def lookup(self, name: str) -> Lookup:
        """Is this dictated name in the book? Never rewrites `name`."""
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
        """The formulary as the mapping prompt sees it — one line per generic.

        Handed to the model for the `known` flag only; the prompt says so in
        capitals and this module overrides whatever it claims anyway. Ordering is
        file order, so the rendered prompt (and therefore the prompt cache) is
        stable across processes.
        """
        return "\n".join(
            f"{drug.generic} [{drug.drug_class}]: {', '.join(drug.brands)}"
            if drug.brands
            else f"{drug.generic} [{drug.drug_class}]"
            for drug in self.drugs
        )


def _parse(payload: dict[str, Any]) -> Formulary:
    drugs = tuple(
        Drug(
            generic=str(row["generic"]),
            drug_class=str(row.get("class", "other")),
            forms=tuple(str(f) for f in row.get("forms", ())),
            brands=tuple(str(b) for b in row.get("brands", ())),
        )
        for row in payload.get("drugs", ())
    )
    return Formulary(version=int(payload.get("version", 1)), drugs=drugs)


@cache
def get_formulary(path: Path | None = None) -> Formulary:
    """The loaded formulary. Cached — the file is data that changes at deploy time."""
    return _parse(json.loads((path or FORMULARY_FILE).read_text(encoding="utf-8")))


def lookup(name: str) -> Lookup:
    """Convenience for the common case: look one name up in the default book."""
    return get_formulary().lookup(name)
