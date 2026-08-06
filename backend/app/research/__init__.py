"""The research assistant — reference for the doctor, authority for nobody (plan §4).

> "The research assistant advises the doctor and only the doctor. It is
> reference-grade, cited where possible, visibly non-authoritative, and it
> **cannot write to any clinical record**." — SESSION-CLINICAL-INTEL-PLAN,
> decision 7

Three modules built before this one produce structure: the spine's signed-note
diagnosis (S10), M1's computed lab flags, M4's confirmed note tags. This module
is the first that *reads* all three, minimises them into something safe to send
to a vendor, shows the doctor exactly that before it sends anything, and holds a
conversation about it.

## The two rules this package is built around

**1. It cannot write to a clinical record.** Structural, in the same shape M4's
"a note cannot prescribe" is structural rather than a check somebody could
forget:

- Nothing here parses the model's answer. `json_output=False` and the reply is
  stored and rendered as **prose** — there is no schema for it to be read into,
  so there is no field on any clinical record for it to reach. Compare
  `app.notes`, which parses into five named fields and can therefore state what
  it refuses to parse into; here the refusal is that there is no parser at all.
- `ResearchThread` / `ResearchTurn` hang off a visit and are read by exactly one
  surface. No other module imports this one.
- **This package does not import `app.prescription`, `app.formulary`,
  `app.dictation`, `app.notes` or `app.checkins`.** Pinned by
  `test_research.py::test_the_research_module_cannot_reach_a_clinical_writer`,
  which reads these files' imports. It is why `assert_visit_scope` in
  `app.research.threads` is a local copy rather than an import of the one
  `app.notes` has — the M4 argument, unchanged: eight duplicated lines are a
  cheaper way to keep two paths genuinely separate than a shared helper that
  quietly couples them.

**2. The doctor sees what leaves the box, and the box decides what can.**
`context.py` assembles the context from named fields in code, never from the
model and never from the client. The panel shows every item before the first
call and the doctor can drop any of them — but a dropped item is dropped by
**id**, and the text is re-derived server-side on every turn. A client cannot
send context text. That is the difference between "the doctor can trim what we
send" and "the browser can send anything it likes to a vendor", and only the
first one is compatible with decision 8.
"""

from app.research.assistant import (
    Assistant,
    BudgetExhausted,
    ResearchError,
    ResearchUnavailable,
)
from app.research.context import ContextItem, ResearchContext, assemble

__all__ = [
    "Assistant",
    "BudgetExhausted",
    "ContextItem",
    "ResearchContext",
    "ResearchError",
    "ResearchUnavailable",
    "assemble",
]
