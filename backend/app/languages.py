"""The pilot's active languages — one source of truth (doc 03 §1).

doc 03 §1 makes language switchable at any time across four tongues: English,
Hindi, and the two S13 completes — Marathi and Telugu. Every patient-facing
surface (the tree bank, the kiosk shell, the WhatsApp templates, the read-back)
must speak all of them, and the language QA harness (`app.lang_qa`) exists to make
"must" a test rather than a hope.

This tuple is that "must", named once. Before S13 it lived only in
`tests/test_tree_bank.py` as `(en, hi)`; promoting it here — and to four — is what
lets the harness, the seed, and the tests agree on what "complete" means without
each carrying its own copy that could drift.

Script families matter for the font stack and the harness's leak check: Hindi and
Marathi are Devanagari, Telugu is its own script, English is Latin. `SCRIPT_RANGES`
gives the harness a way to prove an `mr` string is actually in Devanagari and a
`te` string actually in Telugu — the cheapest catch for "someone pasted the English
in and moved on".
"""

from __future__ import annotations

from app.models.enums import Lang

#: The languages the Alwar pilot ships. Order is presentation order (English last,
#: because the kiosk leads with the local tongues — doc 04 §1).
PILOT_LANGUAGES: tuple[Lang, ...] = (Lang.EN, Lang.HI, Lang.MR, Lang.TE)

#: The Unicode blocks each language is written in. Latin has no single tidy range
#: worth asserting (and English legitimately shares it with numerals everywhere),
#: so it is absent — the leak check only asks the Indic languages to look Indic.
SCRIPT_RANGES: dict[Lang, tuple[tuple[int, int], ...]] = {
    # Devanagari + its extended/vedic supplements.
    Lang.HI: ((0x0900, 0x097F), (0xA8E0, 0xA8FF)),
    Lang.MR: ((0x0900, 0x097F), (0xA8E0, 0xA8FF)),
    # Telugu.
    Lang.TE: ((0x0C00, 0x0C7F),),
}


def looks_like_script(text: str, lang: Lang) -> bool:
    """True if `text` contains at least one character in `lang`'s script.

    Deliberately "at least one", not "all": a real question mixes in digits,
    punctuation, °C and the odd Latin acronym (TB, BP, ESAS), which are correct in
    Marathi and Telugu too. What it catches is the string that is *entirely* Latin —
    an English placeholder left behind — which has no character in the Indic block.
    Languages without a range (English) always pass.
    """
    ranges = SCRIPT_RANGES.get(lang)
    if not ranges:
        return True
    return any(any(lo <= ord(ch) <= hi for lo, hi in ranges) for ch in text)


# -- the runtime script guard -------------------------------------------------
#
# `looks_like_script` above is the *content* check: it runs offline over the
# authored bank and asks "did someone leave the English in?". It is not usable at
# runtime, because it answers False for a perfectly good Hinglish transcript
# ("chest mein pain hai") which contains no Devanagari at all.
#
# What runtime needs is the opposite question, and a narrower one. A model asked
# for Hindi can answer in **Urdu script** — same spoken language, different
# script — and it happens often enough that the Alwar pilot hit it on day one:
# a read-back rendered in Perso-Arabic for a Hindi speaker, which is the
# confirmation step for patients who cannot read, asked in a script they cannot
# read either. The same substitution reached the stored chief complaint through
# STT, so it surfaced on the coordinator console and the board.
#
# So the guard asks: **does this text contain a script that is neither Latin nor
# this language's own?** That is deterministic, and it is the one formulation
# that cannot mis-fire on the two things that are legitimately common here —
# romanised Hinglish, and digits/units/Latin acronyms mixed into Indic text.

#: Named non-Latin script blocks we can plausibly be handed. Latin is deliberately
#: absent: romanised Hindi is normal, legible and must never be rejected.
_SCRIPT_BLOCKS: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    # Arabic + Perso-Arabic supplements — the Urdu case this guard exists for.
    (
        "Arabic",
        ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
    ),
    ("Devanagari", ((0x0900, 0x097F), (0xA8E0, 0xA8FF))),
    ("Bengali", ((0x0980, 0x09FF),)),
    ("Gurmukhi", ((0x0A00, 0x0A7F),)),
    ("Gujarati", ((0x0A80, 0x0AFF),)),
    ("Odia", ((0x0B00, 0x0B7F),)),
    ("Tamil", ((0x0B80, 0x0BFF),)),
    ("Telugu", ((0x0C00, 0x0C7F),)),
    ("Kannada", ((0x0C80, 0x0CFF),)),
    ("Malayalam", ((0x0D00, 0x0D7F),)),
)

#: What each language is allowed to be written in, beyond Latin. A language with
#: no entry (English) is not guarded — an English-selected intake that comes back
#: with a Devanagari quote is a different question from a script substitution, and
#: rejecting it would throw away a patient's own words for no safety gain.
_ALLOWED_SCRIPTS: dict[Lang, frozenset[str]] = {
    Lang.HI: frozenset({"Devanagari"}),
    Lang.MR: frozenset({"Devanagari"}),
    Lang.TE: frozenset({"Telugu"}),
}


def foreign_scripts(text: str, lang: Lang) -> frozenset[str]:
    """The non-Latin scripts in `text` that `lang` is not written in.

    Empty means the text is safe to show, speak or store for this language.
    Latin, digits, punctuation and whitespace are never foreign — they are how
    every real Indian clinical string carries "BP", "38°C" and "5 days".
    """
    allowed = _ALLOWED_SCRIPTS.get(lang)
    if allowed is None:
        return frozenset()
    found = set()
    for ch in text:
        code = ord(ch)
        for name, ranges in _SCRIPT_BLOCKS:
            if name in allowed or name in found:
                continue
            if any(lo <= code <= hi for lo, hi in ranges):
                found.add(name)
                break
    return frozenset(found)


def is_script_safe(text: str, lang: Lang) -> bool:
    """True when nothing in `text` is written in a script `lang` does not use.

    This is the single predicate every model boundary asks before a string
    reaches a patient or the record. It is deliberately cheap and total: no
    model, no network, no configuration, and the same answer every time.
    """
    return not foreign_scripts(text, lang)


def script_problem(text: str, lang: Lang) -> str | None:
    """A log-safe description of the violation, or None. Never includes the text
    itself — a rejected transcript is still a patient's words."""
    wrong = foreign_scripts(text, lang)
    if not wrong:
        return None
    return f"{lang} text contains {'/'.join(sorted(wrong))} script"
