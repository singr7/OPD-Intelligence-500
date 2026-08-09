"""The care-system drift gate — Python's mapping, exported for the TS port.

`make care-system-fixtures` runs this and writes
`web/e2e/fixtures/care-system-conformance.json`; `make test` regenerates and
diffs, so changing `app/care_system.py` without changing
`web/app/_lib/careSystem.ts` fails the build.

## Why this exists

Doc 24 §2 puts the same mapping on both sides of the wire, for the same reason
S7 put the tree walker on both: the kiosk draws department cards with the API
unreachable, and the doctor console decides which sections exist before a second
request lands. Two implementations drift, and this drift is quiet — a console
that hides the cycle sparkline while the server still writes cycle events into
the note produces a doctor who cannot see what they just dictated, and no error
anywhere.

So the TS mapping is not trusted, it is **tested against this file**, exactly
like `app.tree_fixtures`. Smaller than that one by a lot: the mapping is a
lookup table, so the golden trace is the table itself plus the coercion cases
(the unsaid value that means allopathy, and the misspellings that must throw
rather than default).

The snake_case → camelCase rename is recorded here rather than assumed on the TS
side, so a field added in Python and forgotten in TypeScript fails the diff
rather than arriving as `undefined` — which is falsy, and would therefore
silently *hide* a console section rather than announce itself.
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

from app.care_system import CAPABILITIES, CareSystemCapabilities, CareSystemError, care_system_of
from app.models.enums import CareSystem

#: Bumped when the fixture format changes, so a stale file cannot pass quietly.
FIXTURE_VERSION = 1

REPO = Path(__file__).resolve().parents[2]
OUT_PATH = REPO / "web" / "e2e" / "fixtures" / "care-system-conformance.json"

#: Values `care_system_of` must accept, and what they mean. `None` is the
#: payload/seed file that predates doc 24.
_COERCED: tuple[str | None, ...] = (None, "allopathy", "ayurveda")

#: Values it must refuse. Every one of these is a plausible mistake — a typo, a
#: casing slip, the AYUSH ministry's name for the field rather than the system,
#: and a system this platform does not practise yet.
_REFUSED: tuple[str, ...] = ("", "ayurved", "AYURVEDA", "Allopathy", "ayush", "unani", "homeopathy")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.title() for word in rest)


def build() -> dict[str, Any]:
    return {
        "version": FIXTURE_VERSION,
        "generated_by": "backend/app/care_system_fixtures.py (make care-system-fixtures)",
        "note": (
            "The Python capabilities mapping, as the TS port must reproduce it. "
            "Do not hand-edit: regenerate with `make care-system-fixtures` when "
            "app/care_system.py changes."
        ),
        #: Wire name → local name. The TS side asserts its own object uses these
        #: keys, so a field renamed on one side alone cannot pass.
        "field_names": {field.name: _camel(field.name) for field in fields(CareSystemCapabilities)},
        "systems": sorted(member.value for member in CareSystem),
        "capabilities": {
            system.value: caps.to_json() for system, caps in sorted(CAPABILITIES.items())
        },
        "coerced": [
            {"value": value, "expected": care_system_of(value).value} for value in _COERCED
        ],
        "refused": [{"value": value, "reason": _refusal(value)} for value in _REFUSED],
    }


def _refusal(value: str) -> str:
    try:
        care_system_of(value)
    except CareSystemError as exc:
        return str(exc)
    raise AssertionError(  # pragma: no cover - the fixture claims this is refused
        f"care_system_of({value!r}) was accepted; the fixture claims it is invalid"
    )


def _render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    rendered = _render(build())

    if check:
        # Against the file on disk, not against git — same reasoning as
        # `app.tree_fixtures`: the question is whether the golden file describes
        # the mapping as it is right now, which is true or false regardless of
        # what has been committed.
        current = OUT_PATH.read_text() if OUT_PATH.exists() else ""
        if current == rendered:
            return 0
        print(
            f"ERROR: {OUT_PATH.relative_to(REPO)} is stale.\n\n"
            "The care-system capabilities no longer match what Python derives, so\n"
            "web/app/_lib/careSystem.ts is being checked against a mapping that no\n"
            "longer exists. Someone changed app/care_system.py without regenerating.\n\n"
            "  make care-system-fixtures   # then port the change to the TS side\n",
            file=sys.stderr,
        )
        return 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(rendered)
    payload = build()
    print(
        f"wrote {OUT_PATH.relative_to(REPO)}: "
        f"{len(payload['capabilities'])} systems, {len(payload['field_names'])} flags"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
