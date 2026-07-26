"""The language QA harness (doc 06 S13).

Four languages, switchable at any time (doc 03 §1), means every patient-facing
surface has to carry all four — and "has to" is worthless unless something fails
when it doesn't. The tree validator already enforces per-string completeness
*within* a tree; this harness is the layer above it, checking the surfaces the
validator never sees and the failure modes a "the key is present" check misses:

- **Completeness across surfaces.** Every tree declares all four pilot languages;
  every registered WhatsApp template exists in all four; the offline read-back
  template covers all four. (The kiosk shell is gated separately, at compile time,
  by `KioskLang` in `web/.../i18n.ts`.)

- **Script, not just presence.** A Marathi string that is actually English left
  behind passes every "is the `mr` key set?" check. It does not pass "does the
  `mr` string contain a Devanagari character?" — see `looks_like_script`. The same
  catches an `mr`/`te` value byte-identical to its English (a paste that was never
  translated).

- **Glossary consistency.** `seeds/glossary.json` fixes the canonical rendering of
  a few core symptom words. Wherever one of them appears in the bank as a whole
  answer or label, the harness proves the tree used that exact rendering, so
  "fever" cannot be one word on the kiosk and another on WhatsApp.

- **Round-trip smoke.** For each language, the STT→TTS pipeline is exercised end
  to end on the fakes, and `bcp47` is required to yield a real locale (not the
  passthrough that means "we never mapped this language"). This is the cheapest
  proof that a new language is wired through the audio path, not just the text.

Run it as `python -m app.lang_qa` (exits non-zero and prints every problem) or
call `check()` from a test (`tests/test_lang_qa.py`). It reads only repo data and
the provider fakes — no network, no vendor keys — so it belongs in CI next to the
unit tests.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.languages import PILOT_LANGUAGES, looks_like_script
from app.models.enums import Lang
from app.providers.audio import LANG_TO_BCP47, bcp47
from app.providers.stt import FakeSTTProvider
from app.providers.tts import FakeTTSProvider
from app.trees.bank import load_bank
from app.trees.schema import Tree

#: repo-root/seeds/glossary.json.
GLOSSARY_PATH = Path(__file__).resolve().parents[2] / "seeds" / "glossary.json"


@dataclass(frozen=True, slots=True)
class Problem:
    """One thing wrong, phrased for a human reading CI output."""

    surface: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.surface}] {self.detail}"


def load_glossary() -> dict[str, dict[str, str]]:
    data = json.loads(GLOSSARY_PATH.read_text())
    return data["terms"]


def _iter_localized_blocks(tree: Tree) -> Iterable[tuple[str, Mapping[str, str]]]:
    """Every (where, {lang: text}) block a patient can be shown in one tree."""
    yield f"{tree.key}: title", tree.title
    for node in tree.nodes.values():
        yield f"{tree.key}: {node.id}: text", node.text
        for option in node.options:
            yield f"{tree.key}: {node.id}: option {option.id}", option.text
    for flag in tree.red_flags:
        yield f"{tree.key}: flag {flag.id}: label", flag.label
        yield f"{tree.key}: flag {flag.id}: instruction", flag.instruction


def _check_block(surface: str, where: str, block: Mapping[str, str], out: list[Problem]) -> None:
    """Completeness + script + no-English-leak for one localized block."""
    en = block.get(str(Lang.EN), "")
    for lang in PILOT_LANGUAGES:
        text = block.get(str(lang))
        if not text or not text.strip():
            out.append(Problem(surface, f"{where}: missing {lang} text"))
            continue
        if not looks_like_script(text, lang):
            out.append(
                Problem(surface, f"{where}: {lang} text is not in {lang}'s script: {text!r}")
            )
        if lang not in (Lang.EN,) and en and text == en:
            out.append(
                Problem(surface, f"{where}: {lang} text is identical to English (untranslated)")
            )


def _check_trees(out: list[Problem]) -> None:
    bank = load_bank()
    for tree in bank.values():
        if tuple(tree.languages) != PILOT_LANGUAGES:
            out.append(
                Problem(
                    "trees",
                    f"{tree.key}: declares {[str(x) for x in tree.languages]}, "
                    f"expected {[str(x) for x in PILOT_LANGUAGES]}",
                )
            )
        for where, block in _iter_localized_blocks(tree):
            _check_block("trees", where, block, out)


def _check_templates(out: list[Problem]) -> None:
    # Imported lazily so a template import error surfaces as a harness problem, not
    # an import crash of the whole module.
    from app.whatsapp import templates as tpl

    names = {name for (name, _lang) in tpl._REGISTRY}
    for name in sorted(names):
        for lang in PILOT_LANGUAGES:
            try:
                template = tpl.get_template(name, lang)
            except tpl.TemplateError:
                out.append(Problem("whatsapp-templates", f"{name}: no {lang} template registered"))
                continue
            if not looks_like_script(template.body, lang):
                out.append(
                    Problem(
                        "whatsapp-templates",
                        f"{name}/{lang}: body is not in {lang}'s script",
                    )
                )


def _check_protocols(out: list[Problem]) -> None:
    """The S17 check-in bank (doc 03 §9).

    Every question a patient is asked days after her treatment, and every option
    she can tap back, in all four languages. The bank's own loader already
    refuses a missing language; what this adds is the *script* check — hi text
    that is actually romanised, which parses fine and reads as gibberish.
    """
    from app.checkins import protocols as pb

    bank = pb.get_bank()
    for protocol in bank.protocols.values():
        _check_block("protocols", f"{protocol.key}.label", protocol.label, out)
    for qset in bank.question_sets.values():
        _check_block("protocols", f"{qset.key}.title", qset.title, out)
        for question in qset.questions:
            _check_block("protocols", question.id, question.prompt, out)
            for option in question.options:
                _check_block("protocols", f"{question.id}/{option.id}", option.label, out)

    from app.checkins.plan import _PLAIN_MESSAGE

    for lang in PILOT_LANGUAGES:
        text = _PLAIN_MESSAGE.get(lang)
        if not text:
            out.append(Problem("protocols", f"no plain check-in message for {lang}"))
        elif not looks_like_script(text, lang):
            out.append(Problem("protocols", f"{lang} plain check-in message is not in script"))


def _check_readback(out: list[Problem]) -> None:
    from app.intake.summary import _READBACK_TEMPLATE

    for lang in PILOT_LANGUAGES:
        text = _READBACK_TEMPLATE.get(str(lang))
        if not text:
            out.append(Problem("read-back", f"no read-back template for {lang}"))
        elif not looks_like_script(text, lang):
            out.append(Problem("read-back", f"{lang} read-back is not in {lang}'s script"))


def _check_closed_notices(out: list[Problem]) -> None:
    """The channel-closed lines (S-GL.1, doc 12 §7).

    Patient-facing text on a channel the patient chose in her own language, so it
    is held to the same standard as a tree node: present in all four, and actually
    in the right script. It is easy to forget precisely because it is the string
    nobody sees while everything is working.
    """
    from app.channels.state import CLOSED_MESSAGE
    from app.tiers import SWITCHABLE

    for channel in SWITCHABLE:
        lines = CLOSED_MESSAGE.get(channel, {})
        for lang in PILOT_LANGUAGES:
            text = lines.get(lang)
            if not text:
                out.append(Problem("channels", f"no {lang} closed notice for {channel.value}"))
            elif not looks_like_script(text, lang):
                out.append(
                    Problem(
                        "channels",
                        f"{channel.value}: {lang} closed notice is not in {lang}'s script",
                    )
                )


def _check_glossary(out: list[Problem]) -> None:
    glossary = load_glossary()
    # 1. The glossary itself is complete and in-script.
    for concept, block in glossary.items():
        _check_block("glossary", concept, block, out)

    # 2. Wherever a glossary term appears in the bank as a whole English block, the
    #    tree must carry the glossary's exact translation — no synonym drift.
    by_en = {block[str(Lang.EN)]: (concept, block) for concept, block in glossary.items()}
    for tree in load_bank().values():
        for where, block in _iter_localized_blocks(tree):
            match = by_en.get(block.get(str(Lang.EN), ""))
            if match is None:
                continue
            concept, canonical = match
            for lang in PILOT_LANGUAGES:
                want, got = canonical.get(str(lang)), block.get(str(lang))
                if want and got and want != got:
                    out.append(
                        Problem(
                            "glossary",
                            f"{where}: {lang} says {got!r} but glossary term "
                            f"{concept!r} is {want!r}",
                        )
                    )


def _check_bcp47(out: list[Problem]) -> None:
    for lang in PILOT_LANGUAGES:
        code = bcp47(str(lang))
        if str(lang) not in LANG_TO_BCP47 or code == str(lang):
            out.append(
                Problem(
                    "audio",
                    f"{lang} has no BCP-47 mapping — STT/TTS would send a bare "
                    f"{lang!r} the vendor may not accept",
                )
            )


async def _round_trip(lang: Lang) -> str:
    """Synthesize a sample phrase and transcribe it back, on the fakes. Proves the
    audio path accepts the language end to end without a vendor."""
    tts = FakeTTSProvider()
    stt = FakeSTTProvider(script=[f"sample-{lang}"])
    speech = await tts.synthesize(f"नमस्ते {lang}", str(lang))
    transcript = await stt.transcribe(speech.audio, str(lang))
    return transcript.text


def _check_round_trip(out: list[Problem]) -> None:
    for lang in PILOT_LANGUAGES:
        try:
            text = asyncio.run(_round_trip(lang))
        except Exception as exc:  # pragma: no cover - the fakes do not raise
            out.append(Problem("audio", f"{lang}: STT/TTS round-trip raised {exc!r}"))
            continue
        if not text:
            out.append(Problem("audio", f"{lang}: STT/TTS round-trip produced no transcript"))


def check() -> list[Problem]:
    """Run every check and return the full list of problems (empty == healthy)."""
    problems: list[Problem] = []
    _check_trees(problems)
    _check_templates(problems)
    _check_protocols(problems)
    _check_readback(problems)
    _check_closed_notices(problems)
    _check_glossary(problems)
    _check_bcp47(problems)
    _check_round_trip(problems)
    return problems


def main() -> int:
    problems = check()
    if not problems:
        langs = ", ".join(str(lang) for lang in PILOT_LANGUAGES)
        print(f"language QA: clean — all surfaces cover [{langs}]")
        return 0
    print(f"language QA: {len(problems)} problem(s):", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
