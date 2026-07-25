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
