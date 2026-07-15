"""Loads versioned, vendor-neutral prompts from `backend/prompts/` (doc 02 §2).

> "All prompts vendor-neutral in `prompts/`" — doc 02 §2

Prompts are **data, not code**, for the same reason question trees are (doc 02
§4): the people who need to change them — an oncologist rewording a summary
instruction, a coordinator fixing Hindi phrasing — must be able to, and in S18
they will, through the admin console. Text lives in files; this module loads,
validates and renders it.

## Versioning is append-only

`prompts/summarize/v1.md`, `v2.md`, … A published version is **immutable**: to
change a prompt you add the next version. Overwriting one silently reinterprets
every historical output that claims to have been produced by it, and we log the
prompt version onto LLM calls precisely so "why did this summary say that?" is
answerable months later.

## Format

Front-matter (YAML) + body (the user-message template):

    ---
    id: routing
    version: 1
    description: chief complaint -> department
    variables: [complaint, departments]
    response_format: json
    system: |
      You are ...
    ---
    Patient said: {{ complaint }}

`{{ var }}` substitution only — no Jinja, no logic. A prompt with control flow in
it is a program, and it belongs in Python where it can be tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_VERSION_FILE = re.compile(r"^v(\d+)\.md$")
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Prompt:
    """One version of one prompt. Immutable, like the file it came from."""

    id: str
    version: int
    description: str
    system: str
    template: str
    variables: tuple[str, ...]
    response_format: str  # "text" | "json"

    @property
    def ref(self) -> str:
        """Stamped onto LLM calls so an output can be traced to its exact prompt."""
        return f"{self.id}@v{self.version}"

    def render(self, **values: Any) -> str:
        """Fill the template. Strict in both directions.

        Missing variable → error, because a prompt with `{{ complaint }}` left
        literal in it produces a confidently wrong answer rather than a crash.
        Unexpected variable → error, because it means the caller and the prompt
        disagree about the contract and one of them is stale.
        """
        expected = set(self.variables)
        given = set(values)
        if missing := expected - given:
            raise PromptError(f"{self.ref}: missing variables {sorted(missing)}")
        if extra := given - expected:
            raise PromptError(f"{self.ref}: unexpected variables {sorted(extra)}")

        def substitute(match: re.Match[str]) -> str:
            return str(values[match.group(1)])

        return _PLACEHOLDER.sub(substitute, self.template)


def _parse(path: Path) -> Prompt:
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(raw)
    if not match:
        raise PromptError(f"{path}: missing YAML front matter delimited by ---")

    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip()

    for key in ("id", "version", "description"):
        if key not in meta:
            raise PromptError(f"{path}: front matter missing {key!r}")

    declared = tuple(meta.get("variables") or ())
    used = set(_PLACEHOLDER.findall(body))
    # Catches the two ways a prompt file rots: a placeholder nobody passes, and a
    # declared variable the text stopped using.
    if undeclared := used - set(declared):
        raise PromptError(f"{path}: body uses undeclared variables {sorted(undeclared)}")
    if unused := set(declared) - used:
        raise PromptError(f"{path}: declares unused variables {sorted(unused)}")

    version = int(meta["version"])
    file_version = _VERSION_FILE.match(path.name)
    if file_version and int(file_version.group(1)) != version:
        raise PromptError(f"{path}: front matter says v{version} but the filename says {path.name}")

    return Prompt(
        id=str(meta["id"]),
        version=version,
        description=str(meta["description"]),
        system=str(meta.get("system", "")).strip(),
        template=body,
        variables=declared,
        response_format=str(meta.get("response_format", "text")),
    )


@cache
def load(prompt_id: str, version: int | None = None, *, root: Path | None = None) -> Prompt:
    """Load a prompt; latest version unless pinned.

    Callers on a live path should pin (`load("summarize", 3)`) once a version is
    in production: "latest" means the next author's edit changes behaviour on
    deploy, and prompt regressions are quiet.
    """
    directory = (root or PROMPTS_DIR) / prompt_id
    if not directory.is_dir():
        raise PromptError(f"no prompt directory {directory}")

    versions = {
        int(m.group(1)): path
        for path in directory.glob("v*.md")
        if (m := _VERSION_FILE.match(path.name))
    }
    if not versions:
        raise PromptError(f"{directory}: no v<N>.md files")

    chosen = version if version is not None else max(versions)
    if chosen not in versions:
        raise PromptError(f"{prompt_id}: no v{chosen} (have {sorted(versions)})")
    return _parse(versions[chosen])


def all_prompts(*, root: Path | None = None) -> list[Prompt]:
    """Every version of every prompt. Used by the library test and, in S18, the admin console."""
    directory = root or PROMPTS_DIR
    return [
        _parse(path)
        for prompt_dir in sorted(p for p in directory.iterdir() if p.is_dir())
        for path in sorted(prompt_dir.glob("v*.md"))
        if _VERSION_FILE.match(path.name)
    ]
