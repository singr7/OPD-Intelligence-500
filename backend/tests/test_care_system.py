"""The flag, the derivation, and the rule that keeps it one derivation (doc 24 §2).

Three things are pinned here, and they are the whole of SESSION-AYUR-0's
architectural claim:

  1. **The ALLOPATHY row is today's behaviour, bit-for-bit.** Written out as
     literals rather than compared to `CAPABILITIES` — a test that reads the
     mapping to check the mapping passes no matter what somebody changes it to.
  2. **A department is allopathy unless somebody said otherwise**, at the model
     default, at the seed loader and in the column's server default.
  3. **No module outside this one names a `CareSystem` member.** That is the
     property that makes "adding Unani is one enum value and one row" true, and
     it is only true while it is enforced — doc 24 §8's SESSION-AYUR-4 sweep
     should find nothing because this test never let anything land.
"""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from pathlib import Path

import pytest

from app.care_system import (
    CAPABILITIES,
    CareSystemCapabilities,
    CareSystemError,
    capabilities_for,
    care_system_of,
)
from app.models.enums import CareSystem
from app.models.org import Department

# -- 1. the allopathy row is today's behaviour --------------------------------


def test_allopathy_reproduces_todays_behaviour_exactly() -> None:
    """The default row, written as literals.

    Every value here is a statement about code that already exists: the console
    draws a cycle sparkline, the dictation panel lists regimen/cycle events, the
    S17 protocol bank is live, the research tab is framed as NCCN, `validate_meds`
    sees the whole of `seeds/formulary.json`, and there is no assessment panel or
    pathya–apathya section anywhere. Changing any of these is a change to what
    every existing department does, and it should cost a failing test.
    """
    assert capabilities_for(CareSystem.ALLOPATHY) == CareSystemCapabilities(
        shows_cycles=True,
        shows_regimen_events=True,
        checkin_protocols=True,
        guideline_pack="nccn",
        formulary_scope="allopathy",
        ayurveda_assessment=False,
        pathya_apathya=False,
        prompt_pack="oncology",
    )


def test_ayurveda_row_is_the_derived_one() -> None:
    assert capabilities_for(CareSystem.AYURVEDA) == CareSystemCapabilities(
        shows_cycles=False,
        shows_regimen_events=False,
        checkin_protocols=False,
        guideline_pack="ayush",
        formulary_scope="ayurveda",
        ayurveda_assessment=True,
        pathya_apathya=True,
        prompt_pack="ayurveda",
    )


def test_every_system_has_a_row() -> None:
    """A new enum value with no capabilities row is a department whose console
    is decided by whatever `KeyError` happens to reach first."""
    assert set(CAPABILITIES) == set(CareSystem)


def test_the_two_rows_disagree_about_everything() -> None:
    """Not a tautology: a flag identical on both systems is a flag that is not
    doing any work, and should be deleted rather than shipped as configuration
    nobody can change."""
    allopathy = capabilities_for(CareSystem.ALLOPATHY)
    ayurveda = capabilities_for(CareSystem.AYURVEDA)
    for field in fields(CareSystemCapabilities):
        assert getattr(allopathy, field.name) != getattr(ayurveda, field.name), (
            f"{field.name} is the same for both systems — it is not a capability, it is a constant"
        )


def test_capabilities_do_not_carry_the_enum() -> None:
    """Doc 24 §2: consumers get flags, never the value they could branch on.

    A `care_system` field on this object would be read within a session by the
    first component whose need did not quite fit a flag, and that is the whole
    erosion the module exists to prevent. Where the raw value *is* the data (the
    admin selector, a kiosk card's styling) it travels beside this object.
    """
    names = {field.name for field in fields(CareSystemCapabilities)}
    assert "care_system" not in names and "system" not in names


def test_capabilities_are_frozen() -> None:
    caps = capabilities_for(CareSystem.ALLOPATHY)
    with pytest.raises(Exception):  # FrozenInstanceError
        caps.shows_cycles = False  # type: ignore[misc]
    # `replace` is the supported way to make a variant, and it does not mutate.
    assert replace(caps, shows_cycles=False).shows_cycles is False
    assert capabilities_for(CareSystem.ALLOPATHY).shows_cycles is True


def test_the_mapping_itself_cannot_be_written_through() -> None:
    with pytest.raises(TypeError):
        CAPABILITIES[CareSystem.AYURVEDA] = capabilities_for(  # type: ignore[index]
            CareSystem.ALLOPATHY
        )


# -- 2. allopathy unless somebody said otherwise ------------------------------


def test_a_new_department_is_allopathy() -> None:
    """The model default. Doc 24 §3.1: no backfill, because every department
    that predates the second system genuinely is this one."""
    dept = Department(hospital_id=None, name="Medical Oncology", code="MEDONC")
    # SQLAlchemy column defaults are applied at flush; the point being pinned is
    # what that default *is*.
    assert Department.__table__.c.care_system.default.arg is CareSystem.ALLOPATHY
    assert Department.__table__.c.care_system.server_default.arg == "allopathy"
    assert dept.care_system is None or dept.care_system is CareSystem.ALLOPATHY


def test_the_stored_string_is_accepted_as_well_as_the_enum() -> None:
    assert capabilities_for("allopathy") is capabilities_for(CareSystem.ALLOPATHY)
    assert capabilities_for("ayurveda") is capabilities_for(CareSystem.AYURVEDA)


@pytest.mark.parametrize("value", ["", "unani", "Allopathy", "homeopathy", None, 1])
def test_an_unknown_system_raises_rather_than_defaulting(value: object) -> None:
    """Never a silent fallback. A typo that quietly handed an ayurveda clinic
    the chemo check-in machinery would look correct on every screen."""
    with pytest.raises(CareSystemError):
        capabilities_for(value)  # type: ignore[arg-type]


def test_an_unsaid_system_is_allopathy_but_a_misspelt_one_is_not() -> None:
    """`care_system_of` is the seed loader's parse, and the two cases are not
    the same: a `hospital.json` written before doc 24 said nothing and means
    allopathy; a file that says "ayurved" means somebody made a mistake."""
    assert care_system_of(None) is CareSystem.ALLOPATHY
    assert care_system_of("ayurveda") is CareSystem.AYURVEDA
    assert care_system_of(CareSystem.AYURVEDA) is CareSystem.AYURVEDA
    for typo in ("ayurved", "AYURVEDA", "ayush", ""):
        with pytest.raises(CareSystemError):
            care_system_of(typo)


def test_json_shape_is_the_flags_and_nothing_else() -> None:
    payload = capabilities_for(CareSystem.AYURVEDA).to_json()
    assert payload == {
        "shows_cycles": False,
        "shows_regimen_events": False,
        "checkin_protocols": False,
        "guideline_pack": "ayush",
        "formulary_scope": "ayurveda",
        "ayurveda_assessment": True,
        "pathya_apathya": True,
        "prompt_pack": "ayurveda",
    }


# -- 3. one derivation, enforced ----------------------------------------------


#: Where naming a `CareSystem` member is legitimate: the mapping that derives
#: from it, and the column that stores it (which has to state its own default).
#:
#: Notably **not** the seed loader, which parses authored strings through
#: `care_system_of` so the coercion also lives in one place; and not the fixture
#: generator, which iterates the enum rather than naming a member — exporting
#: the mapping needs no permission to branch on it.
_MAY_NAME_THE_ENUM = {
    "app/care_system.py",
    "app/models/org.py",
}

_APP = Path(__file__).resolve().parents[1] / "app"


def _modules_naming_the_enum() -> dict[str, list[str]]:
    """Every module that mentions `CareSystem` at all, and how.

    An AST walk rather than a grep: a comment or a docstring explaining the rule
    should not trip the rule.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP.parent).as_posix()
        source = path.read_text()
        if "CareSystem" not in source:
            continue
        tree = ast.parse(source)
        hits: list[str] = []
        for node in ast.walk(tree):
            # `CareSystem.AYURVEDA` — the member reference a branch is made of.
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "CareSystem"
            ):
                hits.append(f"CareSystem.{node.attr}")
            # `CareSystem("ayurveda")` — the same thing, coerced.
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "CareSystem"
            ):
                hits.append("CareSystem(...)")
        if hits:
            found[rel] = hits
    return found


def test_only_the_mapping_names_a_care_system_member() -> None:
    """The property doc 24 §2 says the executor must not erode.

    "Adding a third system later must be one enum value, one capabilities row,
    and content." That is true exactly while nothing else compares against a
    member — the moment `if dept.care_system is CareSystem.AYURVEDA` appears in a
    route or a service, Unani becomes a repo-wide grep with a clinical
    consequence for every site missed. Read a flag from `capabilities_for`
    instead; if no flag fits, add one to the mapping.
    """
    offenders = {
        module: hits
        for module, hits in _modules_naming_the_enum().items()
        if module not in _MAY_NAME_THE_ENUM
    }
    assert not offenders, (
        "these modules branch on the care-system enum instead of reading a "
        f"capability flag: {offenders}"
    )


def test_the_allowlist_has_no_dead_entries() -> None:
    """A permission nobody uses is a permission somebody will."""
    naming = set(_modules_naming_the_enum())
    stale = {
        module
        for module in _MAY_NAME_THE_ENUM
        if module not in naming and (_APP.parent / module).exists()
    }
    assert not stale, f"remove from the allowlist: {stale}"


def test_the_mapping_imports_nothing_clinical() -> None:
    """`app.care_system` is a lookup table, not a service.

    It must stay importable from a route, a seed script and a fixture generator
    with no database, no provider and no session in sight — which is also what
    keeps it cheap enough that nobody is tempted to cache a derived flag on a
    row somewhere.
    """
    import app.care_system as svc

    tree = ast.parse(Path(svc.__file__).read_text())
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "types",
        "typing",
        "app.models.enums",
    }
