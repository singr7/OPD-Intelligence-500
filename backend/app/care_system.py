"""What a system of medicine *means*, operationally — the only file that knows.

Doc 24 §2. `Department.care_system` is stored once and derived once per side:
here on the server, and in `web/app/_lib/careSystem.ts` in the browser, with a
conformance fixture (`app.care_system_fixtures`) holding the two together the
same way `app.tree_fixtures` holds the two walkers together. Everything
downstream consumes **named capability flags**, never the enum:

    caps = capabilities_for(department.care_system)
    if caps.shows_cycles: ...           # yes
    if department.care_system is AYURVEDA: ...   # no — see below

The rule is not style. A department's system of medicine touches the intake
trees it offers, four or five sections of the doctor console, the formulary a
dictated drug validates against and which system prompt is dispatched. Written
as scattered comparisons, adding Unani or Homeopathy later is a grep across the
whole repo with a clinical consequence for every site missed. Written as one
mapping, it is one enum value, one row in `CAPABILITIES`, and content — which is
the property `tests/test_care_system.py` pins by refusing to let a `CareSystem`
member be named outside this module.

**The ALLOPATHY row is today's behaviour, bit-for-bit.** Every flag on it is
`True`/the oncology value because that is what the console, the dictation panel
and the check-in engine already do for every department that exists. That is
what makes doc 24's "both systems must remain stable" checkable rather than
aspirational: the existing suite passes with no test body edited, because for
allopathy nothing derived here changes anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from dataclasses import fields as dc_fields
from types import MappingProxyType
from typing import Any

from app.models.enums import CareSystem

__all__ = [
    "CAPABILITIES",
    "DEFAULT_CAPABILITIES",
    "DEFAULT_CARE_SYSTEM",
    "FLAG_LABELS",
    "CareSystemCapabilities",
    "CareSystemError",
    "capabilities_for",
    "care_system_of",
    "differences",
]


class CareSystemError(ValueError):
    """An unknown system of medicine. Never a silent fallback to allopathy.

    Defaulting would be the dangerous kindness here: a typo in a seed file or a
    row written by a future migration would quietly hand an ayurveda department
    the oncology prompt pack and the chemo check-in machinery, and nothing would
    look wrong on any screen.
    """


@dataclass(frozen=True, slots=True)
class CareSystemCapabilities:
    """What one system of medicine switches on, as flags a component can read.

    Deliberately **does not carry the `CareSystem` value itself**. A consumer
    holding both would branch on the enum the moment a flag did not quite fit,
    which is the erosion this module exists to prevent. Where the raw value is
    genuinely the data — the admin console's system-of-medicine selector, a
    kiosk card's styling — it travels as its own field beside this object, not
    inside it.
    """

    #: The chemo cycle sparkline (doc 03 §5's "symptom trend across cycles") and
    #: anything else that presumes treatment happens in numbered cycles.
    shows_cycles: bool
    #: Regimen/cycle lines in the dictation panel's event list.
    shows_regimen_events: bool
    #: Whether the S17 check-in protocol machinery surfaces at all. The protocol
    #: bank is six *chemo* regimen families; there is no ayurveda equivalent and
    #: doc 24 puts panchakarma follow-up explicitly out of scope.
    checkin_protocols: bool
    #: Research-tab framing only. A label and a prompt's register — it never
    #: changes what the research assistant is allowed to say or cite.
    guideline_pack: str
    #: Which formulary entries `validate_meds` may call known. An ayurvedic
    #: preparation must not be flagged "not in formulary" in an ayurveda
    #: consult, and a cytotoxic must not become dictatable in one.
    formulary_scope: str
    #: The structured prakriti / agni / nidana note fields (doc 24 §6.1).
    ayurveda_assessment: bool
    #: The pathya–apathya (diet & lifestyle) section of the Rx composer and the
    #: printed prescription (doc 24 §6.2).
    pathya_apathya: bool
    #: Which system-prompt variants the intake summary, dictation mapping and
    #: research assistant dispatch to (doc 24 §6.4).
    prompt_pack: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


#: The whole mapping. Adding a system of medicine is one enum value and one row
#: here — if it ever needs more than that, something downstream has started
#: branching on the enum and should be reading a flag instead.
CAPABILITIES: Mapping[CareSystem, CareSystemCapabilities] = MappingProxyType(
    {
        CareSystem.ALLOPATHY: CareSystemCapabilities(
            shows_cycles=True,
            shows_regimen_events=True,
            checkin_protocols=True,
            guideline_pack="nccn",
            formulary_scope="allopathy",
            ayurveda_assessment=False,
            pathya_apathya=False,
            prompt_pack="oncology",
        ),
        CareSystem.AYURVEDA: CareSystemCapabilities(
            shows_cycles=False,
            shows_regimen_events=False,
            checkin_protocols=False,
            guideline_pack="ayush",
            formulary_scope="ayurveda",
            ayurveda_assessment=True,
            pathya_apathya=True,
            prompt_pack="ayurveda",
        ),
    }
)


#: One plain sentence per flag, in an administrator's words rather than a
#: developer's. This is the vocabulary the admin console's care-system change
#: confirmation is written in (doc 24 §7: "confirmed with explicit copy about
#: what changes"), and it lives here for the reason everything else about a
#: system of medicine does — the module that decides what a flag *is* is the
#: only one that can say what changing it does. `app.facility` diffs two rows
#: and renders these; it never writes a sentence about ayurveda itself, so a
#: third system's confirmation copy comes with its capabilities row.
#:
#: `tests/test_care_system.py` fails if a flag has no label, so a capability
#: added later cannot flip silently under an admin who was never told.
FLAG_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "shows_cycles": "the treatment-cycle trendline on the doctor's console",
        "shows_regimen_events": "regimen and cycle lines in the dictation panel",
        "checkin_protocols": "the follow-up check-in protocols this department can draft",
        "guideline_pack": "the guideline set the research assistant is framed against",
        "formulary_scope": "which preparations a dictated drug is checked against",
        "ayurveda_assessment": "the constitution / agni / nidana note fields",
        "pathya_apathya": "the diet and lifestyle section of the prescription",
        "prompt_pack": "the wording of the intake summary and dictation prompts",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityChange:
    """One flag that a change of system would flip, and what it means."""

    flag: str
    before: Any
    after: Any
    #: `FLAG_LABELS[flag]` — carried alongside so a caller rendering this never
    #: has to reach back into the mapping for the words.
    label: str


def differences(
    before: CareSystemCapabilities, after: CareSystemCapabilities
) -> tuple[CapabilityChange, ...]:
    """Every flag on which two capability rows disagree, in declaration order.

    The admin console's confirmation copy is *derived*, not authored: switching
    a department's system of medicine changes exactly the flags this returns,
    and an operator is shown that list rather than a paragraph somebody wrote
    once and forgot to update when a flag was added.
    """
    return tuple(
        CapabilityChange(
            flag=field.name,
            before=getattr(before, field.name),
            after=getattr(after, field.name),
            label=FLAG_LABELS[field.name],
        )
        for field in dc_fields(CareSystemCapabilities)
        if getattr(before, field.name) != getattr(after, field.name)
    )


#: What a caller with no department in hand gets.
#:
#: Exists so that "today's behaviour, bit-for-bit" can be *named* by a service
#: which must not name the enum — a `DictationMapper` built by a script, a
#: summarizer for a tree with no department of its own. Writing
#: `capabilities_for(CareSystem.ALLOPATHY)` at those sites would spell the same
#: thing and cost the property doc 24 §2 asks for: `tests/test_care_system.py`
#: fails the moment a module outside this one names a member, because that is the
#: first step of the erosion it guards against, whether or not the line is a
#: branch today.
DEFAULT_CARE_SYSTEM: CareSystem = CareSystem.ALLOPATHY

#: `capabilities_for(DEFAULT_CARE_SYSTEM)`, resolved once. The default argument
#: for every service that takes capabilities and can be called without them.
DEFAULT_CAPABILITIES: CareSystemCapabilities = CAPABILITIES[DEFAULT_CARE_SYSTEM]


def care_system_of(value: CareSystem | str | None) -> CareSystem:
    """One authored value — from a seed file, a JSON body — as the stored enum.

    `None` means the author did not say, and the answer is allopathy: doc 24 §3.4
    wants a third-party `hospital.json` written before any of this existed to
    keep loading, and a department nobody has classified is the system this
    platform has always practised. An unknown *string*, on the other hand, is a
    mistake and raises — "ayurved", "AYURVEDA" and "ayush" must not all quietly
    become allopathy.

    Exists so that the seed loader (and every future parser) never has to name
    the enum: this module stays the only file that does.
    """
    if value is None:
        return CareSystem.ALLOPATHY
    try:
        return CareSystem(value)
    except ValueError as exc:
        raise CareSystemError(
            f"unknown system of medicine {value!r}; "
            f"expected one of {[member.value for member in CareSystem]}"
        ) from exc


def capabilities_for(value: CareSystem | str) -> CareSystemCapabilities:
    """The capability row for one system of medicine.

    Accepts the stored string as well as the enum, because that is what arrives
    from a seed file, a JSON body and a database row, and one coercion here
    beats a `CareSystem(...)` call at every call site — each of which would be a
    place the enum gets named again.
    """
    system = care_system_of(value) if value is not None else None
    if system is None:
        raise CareSystemError("no system of medicine given")
    try:
        return CAPABILITIES[system]
    except KeyError as exc:  # pragma: no cover - the completeness test forbids it
        raise CareSystemError(f"no capabilities row for {system.value!r}") from exc
