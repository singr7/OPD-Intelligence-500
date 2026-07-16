"""Loads the authored tree bank from `seeds/trees/` (doc 03 §3).

The bank on disk is the *authoring* source: files a clinician can review in a pull
request and `app.seed` loads into `question_trees`. Once S18 ships its editor, the
database is the live source and these files are the pilot's starting content —
the same relationship `seeds/price_book.json` has to `price_book`.

Every file is parsed through `schema.parse`, so importing the bank is itself the
proof that the authored content is valid. A malformed tree fails the test suite
and the seed, not a patient's intake.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from app.trees.schema import Tree, TreeError, parse

#: repo-root/seeds/trees — alongside the other seed data (`app.seed.SEEDS_DIR`).
TREES_DIR = Path(__file__).resolve().parents[3] / "seeds" / "trees"


def load_file(path: Path) -> Tree:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise TreeError(f"{path.name}: not valid JSON: {exc}") from exc
    try:
        tree = parse(data)
    except TreeError as exc:
        raise TreeError(f"{path.name}: {exc}") from exc
    if tree.key != path.stem:
        raise TreeError(f"{path.name}: tree key {tree.key!r} does not match the filename")
    return tree


@cache
def load_bank(root: Path | None = None) -> dict[str, Tree]:
    """Every authored tree, keyed by `key`. Cached — trees are immutable data.

    One file per tree key, named `<key>.json`. Versions are not files: a tree's
    `version` is bumped in place when the content changes, because the published
    *rows* in `question_trees` are what carry history (draft → published, per
    doc 02 §4), and a directory of `v1.json`/`v2.json` would be a second, quietly
    diverging version system next to the table's.
    """
    directory = root or TREES_DIR
    if not directory.is_dir():
        raise TreeError(f"no tree bank at {directory}")
    trees: dict[str, Tree] = {}
    for path in sorted(directory.glob("*.json")):
        tree = load_file(path)
        if tree.key in trees:  # pragma: no cover - filenames are unique on disk
            raise TreeError(f"duplicate tree key {tree.key!r}")
        trees[tree.key] = tree
    if not trees:
        raise TreeError(f"tree bank at {directory} is empty")
    return trees


def get(key: str, root: Path | None = None) -> Tree:
    bank = load_bank(root)
    try:
        return bank[key]
    except KeyError:
        raise TreeError(f"no tree {key!r}; bank has {sorted(bank)}") from None


def for_department(code: str, root: Path | None = None) -> list[Tree]:
    """Trees belonging to a department code, e.g. "MEDONC". The routing classifier
    picks a department; this is how S5 turns that into something to ask."""
    return [tree for tree in load_bank(root).values() if tree.department == code]
