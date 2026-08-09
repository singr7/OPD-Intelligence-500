"""Hospital identity + the department registry (AYUR-1, doc 24 §7).

Three claims carry this session, and they are the first three blocks below.

**A rename reaches the paper.** Doc 24 §3.2 said the letterhead "already reads
stored hospital facts, so 'Ayurveda Hospital' propagates for free — verify with
the pass and Rx print tests, **don't assume**." Half of that was true. The
prescription letterhead does read `Hospital.name`; the kiosk's boarding pass
rendered a four-language constant compiled into the bundle that had *already*
drifted from the seeded name. So there are two tests here, not one: the
prescription, and `GET /kiosk/bundle` — which is where the kiosk (and therefore
the pass, online and during an outage) now gets the name from.

**A department cannot be opened onto an error.** `routes/kiosk.py` asserts a tree
was resolved after routing, so an active department with no intake tree is a
patient tapping a card into a 500. Doc 24 seeded `AYUR` dark for exactly this
reason, and this check is what kept it dark until SESSION-AYUR-2 authored its
trees — which it now has, so the example moved to `CODE_WITHOUT_A_TREE` and the
guard is unchanged.

**A change of system of medicine is confirmed against derived consequences.**
The copy is not authored — it is the diff of two capability rows, so a flag
added in a later session appears in the confirmation without anyone editing a
paragraph.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import facility
from app.auth.tokens import create_access_token
from app.care_system import CAPABILITIES, FLAG_LABELS, CareSystemCapabilities
from app.config import Settings
from app.models.audit import AuditLog
from app.models.enums import Lang, Role
from app.models.org import Department, Hospital
from tests import factories as f

pytestmark = pytest.mark.asyncio

#: A department code the disk tree bank actually has a tree for, so `resolve_tree`
#: finds something and the activation guard passes. Using a real one rather than
#: publishing a row keeps these tests about the facility, not about the editor.
CODE_WITH_A_TREE = "MEDONC"
#: A department the tree bank has nothing for. It used to be "AYUR", which is
#: what doc 24 seeded dark; SESSION-AYUR-2 authored the five ayurveda trees, so
#: AYUR now *has* an intake and this check needs a department that still does
#: not. That change of example is the point: the guard is about trees, not about
#: ayurveda.
CODE_WITHOUT_A_TREE = "PHYSIO"


async def _admin(session: AsyncSession, settings: Settings, hospital: Hospital) -> dict[str, str]:
    user = f.make_user(hospital, role=Role.ADMIN)
    session.add(user)
    await session.flush()
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def _one_hospital(session: AsyncSession) -> Hospital:
    """A single hospital, which is what `app.facility` assumes and the seed makes."""
    hospital = f.make_hospital(name="Alwar District Cancer Centre")
    session.add(hospital)
    await session.flush()
    return hospital


# -- 1. the rename reaches the paper -------------------------------------------


async def test_renaming_the_hospital_reprints_the_prescription_letterhead(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """The session's headline AC, asserted against the sheet a pharmacy reads.

    Not against the PATCH's own response — that would prove only that the route
    echoes what it was sent. The prescription is re-fetched *after* the rename
    and the old name must be gone from it, because a letterhead that carries both
    is a letterhead that carries a stale one somewhere.
    """
    from app import prescription as rx
    from tests.test_prescription import _headers, _signed

    clinic, _visit, dictation = await _signed(session)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None
    was = clinic["hospital"].name

    renamed = await client.patch(
        "/admin/hospital",
        headers=await _admin(session, settings, clinic["hospital"]),
        json={"name": "Alwar Ayurveda Hospital"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Alwar Ayurveda Hospital"

    printed = await client.get(
        f"/prescriptions/{prescription.id}/print?copy=clinical",
        headers=_headers(settings, clinic["user"]),
    )
    assert printed.status_code == 200
    assert "Alwar Ayurveda Hospital" in printed.text
    assert was not in printed.text


async def test_the_kiosk_bundle_carries_the_stored_name_so_the_pass_prints_it(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """The half doc 24 §3.2 told us not to assume — and which was not true.

    The boarding pass renders whatever the kiosk hands `layoutPass`, and until
    this session that was a constant compiled into the bundle. The pass has no
    server round-trip of its own, so the bundle is where the name has to arrive:
    it is fetched while the network is up and cached, which is also what makes
    the name survive the outage the pass exists for.
    """
    hospital = await _one_hospital(session)

    before = await client.get("/kiosk/bundle")
    assert before.status_code == 200
    assert before.json()["hospital"]["name"] == "Alwar District Cancer Centre"

    await client.patch(
        "/admin/hospital",
        headers=await _admin(session, settings, hospital),
        json={"name": "Alwar Ayurveda Hospital", "city": "Alwar"},
    )

    after = await client.get("/kiosk/bundle")
    assert after.json()["hospital"] == {
        "name": "Alwar Ayurveda Hospital",
        "name_i18n": {},
        "city": "Alwar",
    }


async def test_a_rename_invalidates_a_cached_offline_bundle(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """Or the kiosk keeps printing the old name through the next outage.

    Same reasoning that put `care_system` in the hash in AYUR-0: anything the
    kiosk *draws* from the bundle has to be in the bundle's ETag, because the
    kiosk skips the download when the ETag matches.
    """
    hospital = await _one_hospital(session)
    first = (await client.get("/kiosk/bundle")).json()["etag"]

    await client.patch(
        "/admin/hospital",
        headers=await _admin(session, settings, hospital),
        json={"name": "Alwar Ayurveda Hospital"},
    )

    assert (await client.get("/kiosk/bundle")).json()["etag"] != first


# -- 1b. the hospital's name in the language the reader chose -------------------


async def test_the_stored_translation_wins_and_english_is_the_fallback(
    session: AsyncSession,
) -> None:
    """`name_in` is the one derivation, and there is no call site left that reads
    `hospital.name` while holding a language."""
    hospital = await _one_hospital(session)
    hospital.name_i18n = {"hi": "अलवर जिला कैंसर केंद्र"}
    await session.flush()

    assert hospital.name_in(Lang.HI) == "अलवर जिला कैंसर केंद्र"
    # Untranslated languages show the real name, not a blank and not a key.
    assert hospital.name_in(Lang.TE) == "Alwar District Cancer Centre"
    assert hospital.name_in(Lang.EN) == "Alwar District Cancer Centre"
    assert hospital.name_in(None) == "Alwar District Cancer Centre"


async def test_a_hospital_nobody_has_translated_renders_exactly_as_before(
    session: AsyncSession,
) -> None:
    """The migration ships no backfill, so this is every existing row. It has to
    be indistinguishable from the behaviour before the column existed."""
    hospital = await _one_hospital(session)

    assert hospital.name_i18n == {}
    assert {hospital.name_in(lang) for lang in (Lang.EN, Lang.HI, Lang.MR, Lang.TE)} == {
        "Alwar District Cancer Centre"
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"gu": "કંઈક"},  # a language this hospital does not serve at all
        {"hin": "अलवर"},  # a plausible typo for hi
        {"en": "Alwar District Cancer Centre"},  # English lives in `name`
        {"hi": "Alwar District Cancer Centre"},  # the English name pasted across
        {"hi": 42},
        "not a mapping",
    ],
)
async def test_a_translation_that_would_never_render_is_refused(bad: object) -> None:
    """Each of these stores fine and then fails silently at render time — the
    letterhead simply keeps showing English to the one person who needed
    otherwise, with nothing anywhere to say why."""
    with pytest.raises(facility.FacilityError):
        facility.parse_name_i18n(bad)


@pytest.mark.parametrize("lang", ["mr", "te"])
async def test_a_language_this_hospital_has_not_translated_itself_into_is_refused(
    lang: str,
) -> None:
    """Hindi only for now, by the operator's decision — mr/te fall back to
    English rather than carry a guess at a facility's own name.

    Refused rather than quietly ignored: a Marathi name accepted here would sit
    in the column, never render, and give an administrator every reason to
    believe the job was done.
    """
    with pytest.raises(facility.FacilityError) as raised:
        facility.parse_name_i18n({lang: "अलवर जिल्हा कर्करोग केंद्र"})

    assert "not translated into" in str(raised.value)


async def test_widening_the_languages_is_one_tuple_entry() -> None:
    """The property that keeps "add Marathi later" from being a code change.

    Everything downstream — the column, the console's fields, the kiosk's
    per-language fallback — reads this tuple or the map it produces; nothing
    names Hindi.
    """
    assert facility.TRANSLATABLE_LANGUAGES == (Lang.HI,)
    assert Lang.EN not in facility.TRANSLATABLE_LANGUAGES, "English is `name` itself"


async def test_a_blank_translation_is_dropped_rather_than_stored(session: AsyncSession) -> None:
    """Absent and empty must mean the same thing, or `name_in` grows a second way
    to fall back and one of them eventually gets it wrong."""
    await _one_hospital(session)

    updated = await facility.update_identity(session, name_i18n={"hi": "   "})

    assert updated.name_i18n == {}


async def test_a_translation_can_be_removed(session: AsyncSession) -> None:
    """The map replaces rather than merges. A hospital that renames itself must
    be able to drop a translation of the old name, not just add to it."""
    await _one_hospital(session)
    await facility.update_identity(session, name_i18n={"hi": "अलवर"})

    updated = await facility.update_identity(session, name_i18n={})

    assert updated.name_i18n == {}


async def test_the_kiosk_bundle_carries_every_language_at_once(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """Not the one language this request is in: the kiosk switches language
    client-side with no server round trip, and an outage must not catch it
    holding only the language the last patient happened to pick."""
    hospital = await _one_hospital(session)

    await client.patch(
        "/admin/hospital",
        headers=await _admin(session, settings, hospital),
        json={"name_i18n": {"hi": "अलवर जिला कैंसर केंद्र"}},
    )

    bundle = (await client.get("/kiosk/bundle")).json()["hospital"]
    assert bundle["name"] == "Alwar District Cancer Centre"
    assert bundle["name_i18n"] == {"hi": "अलवर जिला कैंसर केंद्र"}


async def test_a_new_translation_invalidates_a_cached_offline_bundle(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    hospital = await _one_hospital(session)
    first = (await client.get("/kiosk/bundle")).json()["etag"]

    await client.patch(
        "/admin/hospital",
        headers=await _admin(session, settings, hospital),
        json={"name_i18n": {"hi": "अलवर जिला कैंसर केंद्र"}},
    )

    assert (await client.get("/kiosk/bundle")).json()["etag"] != first


async def test_the_patient_copy_of_a_prescription_is_headed_in_her_language(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """And the clinical copy is not.

    The patient copy is hers; the clinical copy is the file's and the pharmacy's,
    gets photocopied into a chart, and stays in the name everyone can read.
    """
    from app import prescription as rx
    from tests.test_prescription import _headers, _signed

    clinic, _visit, dictation = await _signed(session)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None
    clinic["hospital"].name_i18n = {"hi": "अलवर जिला कैंसर केंद्र"}
    await session.flush()
    headers = _headers(settings, clinic["user"])

    patient_copy = await client.get(
        f"/prescriptions/{prescription.id}/print?copy=patient&lang=hi", headers=headers
    )
    clinical_copy = await client.get(
        f"/prescriptions/{prescription.id}/print?copy=clinical", headers=headers
    )

    assert "अलवर जिला कैंसर केंद्र" in patient_copy.text
    assert "अलवर जिला कैंसर केंद्र" not in clinical_copy.text
    assert clinic["hospital"].name in clinical_copy.text


async def test_a_staff_invite_names_the_hospital_in_the_reader_s_language(
    session: AsyncSession, sms
) -> None:
    """One of sixteen call sites the sweep rewrote — asserted through the SMS a
    person actually receives rather than through the helper."""
    from app import people

    hospital = await _one_hospital(session)
    hospital.name_i18n = {"hi": "अलवर जिला कैंसर केंद्र"}
    await session.flush()
    user = f.make_user(hospital, role=Role.COORDINATOR, lang=Lang.HI)
    session.add(user)
    await session.flush()

    await people.send_invite(session, user_id=user.id, settings=Settings())

    assert "अलवर जिला कैंसर केंद्र" in sms.sent[-1].body


# -- 2. a department cannot be opened onto an error ----------------------------


async def test_a_department_with_no_intake_tree_cannot_be_opened(
    session: AsyncSession,
) -> None:
    """The guard doc 24's AYUR department was seeded dark behind.

    `routes/kiosk.py` asserts `routed.tree is not None` after routing, and the
    chooser lists every active department — so this refusal is the difference
    between "Physiotherapy is not open yet" and a patient's tap returning a 500.
    """
    hospital = await _one_hospital(session)
    dept = f.make_department(hospital, code=CODE_WITHOUT_A_TREE, name="Physiotherapy", active=False)
    session.add(dept)
    await session.flush()

    with pytest.raises(facility.FacilityError) as raised:
        await facility.update_department(session, code=CODE_WITHOUT_A_TREE, active=True)

    assert "no intake tree" in str(raised.value)
    assert CODE_WITHOUT_A_TREE in str(raised.value)
    await session.refresh(dept)
    assert dept.active is False


async def test_a_department_that_has_a_tree_opens(session: AsyncSession) -> None:
    hospital = await _one_hospital(session)
    dept = f.make_department(hospital, code=CODE_WITH_A_TREE, active=False)
    session.add(dept)
    await session.flush()

    updated = await facility.update_department(session, code=CODE_WITH_A_TREE, active=True)

    assert updated.active is True
    assert updated.has_intake is True


async def test_closing_a_department_is_never_blocked(session: AsyncSession) -> None:
    """The guard is one-directional on purpose. Opening a department exposes
    patients to it; closing one only stops that, and an operator taking a
    department off the kiosk in a hurry must never be argued with."""
    hospital = await _one_hospital(session)
    dept = f.make_department(hospital, code=CODE_WITHOUT_A_TREE, name="Physiotherapy", active=True)
    session.add(dept)
    await session.flush()

    updated = await facility.update_department(session, code=CODE_WITHOUT_A_TREE, active=False)

    assert updated.active is False
    assert updated.has_intake is False


async def test_a_new_department_is_created_closed_and_allopathic(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    hospital = await _one_hospital(session)

    resp = await client.post(
        "/admin/departments",
        headers=await _admin(session, settings, hospital),
        json={"code": "physio", "name": "Physiotherapy"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["code"] == "PHYSIO"  # normalised, because it is a natural key
    assert body["active"] is False
    assert body["care_system"] == "allopathy"
    assert body["has_intake"] is False


async def test_creating_a_department_open_is_refused_for_the_same_reason(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    hospital = await _one_hospital(session)

    resp = await client.post(
        "/admin/departments",
        headers=await _admin(session, settings, hospital),
        json={"code": "physio", "name": "Physiotherapy", "active": True},
    )

    assert resp.status_code == 422
    assert "no intake tree" in resp.json()["detail"]
    assert (
        await session.scalar(select(Department).where(Department.code == CODE_WITHOUT_A_TREE))
        is None
    )


@pytest.mark.parametrize("code", ["", "a", "1AYUR", "AYUR-2", "ayur veda"])
async def test_a_department_code_that_is_not_a_natural_key_is_refused(
    session: AsyncSession, code: str
) -> None:
    await _one_hospital(session)
    with pytest.raises(facility.FacilityError):
        await facility.create_department(session, code=code, name="Something")


async def test_a_duplicate_department_code_is_refused_by_name(session: AsyncSession) -> None:
    """Explicit rather than "already exists": the code an admin is retyping
    usually belongs to a department they can go and look at."""
    hospital = await _one_hospital(session)
    session.add(f.make_department(hospital, code="AYUR", name="Ayurveda"))
    await session.flush()

    with pytest.raises(facility.FacilityError) as raised:
        await facility.create_department(session, code="AYUR", name="Ayurveda II")

    assert "Ayurveda" in str(raised.value)


async def test_a_misspelt_system_of_medicine_is_refused_not_defaulted(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """Same rule the seed loader has (doc 24 §3.4). Defaulting "ayurved" would
    hand an ayurveda clinic the oncology prompt pack and look right on every
    screen."""
    hospital = await _one_hospital(session)

    resp = await client.post(
        "/admin/departments",
        headers=await _admin(session, settings, hospital),
        json={"code": "AYUR", "name": "Ayurveda", "care_system": "ayurved"},
    )

    assert resp.status_code == 422
    assert "unknown system of medicine" in resp.json()["detail"]


# -- 3. changing the system of medicine ----------------------------------------


async def test_changing_the_system_of_medicine_needs_an_acknowledgement(
    session: AsyncSession,
) -> None:
    hospital = await _one_hospital(session)
    dept = f.make_department(hospital, code="AYUR", name="Ayurveda")
    session.add(dept)
    await session.flush()

    with pytest.raises(facility.FacilityError) as raised:
        await facility.update_department(session, code="AYUR", care_system="ayurveda")

    assert "Confirm" in str(raised.value)
    await session.refresh(dept)
    assert str(dept.care_system) == "allopathy"


async def test_an_acknowledged_change_lands(session: AsyncSession) -> None:
    hospital = await _one_hospital(session)
    session.add(f.make_department(hospital, code="AYUR", name="Ayurveda"))
    await session.flush()

    updated = await facility.update_department(
        session, code="AYUR", care_system="ayurveda", acknowledge=True
    )

    assert updated.care_system == "ayurveda"


async def test_renaming_a_department_needs_no_acknowledgement(session: AsyncSession) -> None:
    """The confirmation is for the system of medicine, not for every edit. A
    console that asks twice for a typo fix teaches an operator to click through
    the one that matters."""
    hospital = await _one_hospital(session)
    session.add(f.make_department(hospital, code="AYUR", name="Ayurved"))
    await session.flush()

    updated = await facility.update_department(
        session, code="AYUR", name="Ayurveda", care_system="allopathy"
    )

    assert updated.name == "Ayurveda"


async def test_the_impact_is_derived_from_the_two_capability_rows(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """Doc 24 §7's "explicit copy about what changes", as data rather than prose.

    The assertion is against `CAPABILITIES` itself, so a capability added in a
    later session appears in this confirmation automatically — and if somebody
    adds one that the impact endpoint drops, this fails.
    """
    hospital = await _one_hospital(session)
    session.add(f.make_department(hospital, code="AYUR", name="Ayurveda"))
    await session.flush()

    resp = await client.get(
        "/admin/departments/AYUR/care-system-impact?to=ayurveda",
        headers=await _admin(session, settings, hospital),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_a_change"] is True
    assert body["from_system"] == "allopathy" and body["to_system"] == "ayurveda"

    expected = {
        field
        for field in CareSystemCapabilities.__dataclass_fields__
        if getattr(CAPABILITIES["allopathy"], field) != getattr(CAPABILITIES["ayurveda"], field)
    }
    assert {change["flag"] for change in body["changes"]} == expected
    # Every line an administrator reads is a sentence, not a field name.
    assert all(change["label"] and change["label"] != change["flag"] for change in body["changes"])


async def test_an_impact_for_the_system_it_already_practises_is_not_a_change(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    hospital = await _one_hospital(session)
    session.add(f.make_department(hospital, code="AYUR", name="Ayurveda"))
    await session.flush()

    resp = await client.get(
        "/admin/departments/AYUR/care-system-impact?to=allopathy",
        headers=await _admin(session, settings, hospital),
    )

    body = resp.json()
    assert body["is_a_change"] is False
    assert body["changes"] == []


async def test_every_capability_flag_has_a_sentence(session: AsyncSession) -> None:
    """A flag with no label would be a consequence an operator is never told
    about, which is the failure the confirmation exists to prevent."""
    assert set(FLAG_LABELS) == set(CareSystemCapabilities.__dataclass_fields__)


# -- 4. audit ------------------------------------------------------------------


async def _audit(session: AsyncSession, entity: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AuditLog).where(AuditLog.entity == entity).order_by(AuditLog.at)
        )
    ).scalars()
    return [row.meta for row in rows]


async def test_a_rename_is_audited(session: AsyncSession) -> None:
    await _one_hospital(session)

    await facility.update_identity(session, name="Alwar Ayurveda Hospital")

    entries = await _audit(session, "hospitals")
    assert entries and entries[-1]["changed"] == ["name"]
    assert entries[-1]["edited_from"] == "console"


async def test_a_rename_to_the_same_name_writes_no_audit_row(session: AsyncSession) -> None:
    """An audit trail padded with no-ops is one nobody reads."""
    hospital = await _one_hospital(session)

    await facility.update_identity(session, name=hospital.name)

    assert await _audit(session, "hospitals") == []


async def test_an_absent_field_is_left_alone_not_blanked(session: AsyncSession) -> None:
    hospital = await _one_hospital(session)
    hospital.district = "Alwar"
    await session.flush()

    await facility.update_identity(session, name="Alwar Ayurveda Hospital")

    await session.refresh(hospital)
    assert hospital.district == "Alwar"


async def test_a_hospital_cannot_be_left_nameless(session: AsyncSession) -> None:
    await _one_hospital(session)
    with pytest.raises(facility.FacilityError):
        await facility.update_identity(session, name="   ")


async def test_the_system_of_medicine_change_is_audited_with_both_ends(
    session: AsyncSession,
) -> None:
    """ "Who switched Ayurveda on, and when" is the question this trail answers,
    so the values go in verbatim rather than as a redaction marker — neither is
    PII, and a `<redacted>` here would make the row useless."""
    hospital = await _one_hospital(session)
    session.add(f.make_department(hospital, code="AYUR", name="Ayurveda"))
    await session.flush()

    await facility.update_department(session, code="AYUR", care_system="ayurveda", acknowledge=True)

    entries = await _audit(session, "departments")
    assert entries[-1]["changed"]["care_system"] == "allopathy->ayurveda"
    assert entries[-1]["code"] == "AYUR"


async def test_opening_a_department_is_audited(session: AsyncSession) -> None:
    hospital = await _one_hospital(session)
    session.add(f.make_department(hospital, code=CODE_WITH_A_TREE, active=False))
    await session.flush()

    await facility.update_department(session, code=CODE_WITH_A_TREE, active=True)

    entries = await _audit(session, "departments")
    assert entries[-1]["changed"]["active"] is True


async def test_creating_a_department_is_audited(session: AsyncSession) -> None:
    await _one_hospital(session)

    await facility.create_department(session, code="AYUR", name="Ayurveda", care_system="ayurveda")

    entries = await _audit(session, "departments")
    assert entries[-1]["care_system"] == "ayurveda"
    assert entries[-1]["created_from"] == "console"


# -- 5. the two reads stay apart -----------------------------------------------


async def test_the_editor_sees_closed_departments_and_the_doctor_picker_does_not(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """`GET /admin/departments` feeds the create-a-doctor form and stays
    active-only — a console must not be able to hire somebody into a department
    no patient can reach. `GET /admin/facility` is the editor's read, and
    opening the closed ones is what it is for."""
    hospital = await _one_hospital(session)
    session.add_all(
        [
            f.make_department(hospital, code="AYUR", name="Ayurveda", active=False),
            f.make_department(hospital, code=CODE_WITH_A_TREE, name="Medical Oncology"),
        ]
    )
    await session.flush()
    headers = await _admin(session, settings, hospital)

    picker = (await client.get("/admin/departments", headers=headers)).json()
    editor = (await client.get("/admin/facility", headers=headers)).json()

    assert "AYUR" not in {d["code"] for d in picker}
    assert "AYUR" in {d["code"] for d in editor["departments"]}
    assert editor["hospital"]["name"] == hospital.name


async def test_the_editor_counts_the_doctors_a_change_would_affect(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)

    rows = {row.code: row for row in await facility.list_departments(session)}

    assert rows[clinic["department"].code].doctors == 1


async def test_the_facility_routes_are_admin_only(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    hospital = await _one_hospital(session)
    nurse = f.make_user(hospital, role=Role.NURSE)
    session.add(nurse)
    await session.flush()
    token = create_access_token(
        user_id=nurse.id,
        role=nurse.role,
        name=nurse.name,
        settings=settings,
        hospital_id=nurse.hospital_id,
    ).token
    headers = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/admin/facility", headers=headers)).status_code == 403
    assert (await client.patch("/admin/hospital", headers=headers, json={})).status_code == 403
    assert (await client.post("/admin/departments", headers=headers, json={})).status_code == 403
    assert (
        await client.patch("/admin/departments/AYUR", headers=headers, json={})
    ).status_code == 403


async def test_the_default_language_is_editable_and_stored(session: AsyncSession) -> None:
    hospital = await _one_hospital(session)

    updated = await facility.update_identity(session, default_lang=Lang.MR)

    assert updated.default_lang is Lang.MR
    await session.refresh(hospital)
    assert hospital.default_lang is Lang.MR
