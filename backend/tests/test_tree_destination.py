"""Where a completed walk says the patient belongs (doc 24 §4/§5).

`test_tree_bank.py` proves the ayurveda content is well formed; this file proves
the one piece of *machinery* SESSION-AYUR-2 added, which is the ability of a tree
to name a department other than the one the intake started in.

Three things are load-bearing and each has a test that fails loudly if it erodes:

- **A red flag outranks a preference.** A patient who asked for the ayurveda OPD
  and then reported chest pain is not moved on the strength of the asking. Doc 24
  §4: a wellness framing must never soften an emergency.
- **The TB rule routes.** TB is notifiable. Blood in the sputum, or two weeks of
  cough with evening fever or weight loss, has to reach Pulmonology/DOTS out of
  the *ayurveda* respiratory tree, with the ayurveda visit adjunct at most.
- **A closed department is not offered.** Not greyed out, not refused on tap —
  the question is gone before the tree leaves the server, so an offline kiosk
  cannot have cached it either.
"""

from __future__ import annotations

import pytest

from app.trees import visibility
from app.trees.bank import get, load_bank
from app.trees.schema import TreeError, parse
from app.trees.walker import Walk

ALL_DEPARTMENTS = {
    "GENMED",
    "PULM",
    "AYUR",
    "MEDONC",
    "RADONC",
    "SURGONC",
    "PALL",
    "GYNAE",
    "ENT",
    "DERM",
}


def walk_genmed(**answers) -> Walk:
    """A GENMED walk-in, answered as far as asked."""
    walk = Walk(get("general_medicine_routing"))
    for node_id, value in answers.items():
        walk.save(node_id.replace("__", "."), value)
    return walk


# -- 1. the preference ---------------------------------------------------------


def test_asking_for_ayurveda_sends_the_visit_to_ayurveda():
    """doc 24 §5 — the offer in the allopathic routing trees."""
    walk = walk_genmed(gm__problem="weakness", gm__duration=5, gm__severity=4, gm__ayur="ayurveda")
    assert walk.destination() == "AYUR"


def test_declining_the_offer_leaves_the_patient_where_they_are():
    walk = walk_genmed(gm__problem="weakness", gm__duration=5, gm__severity=4, gm__ayur="regular")
    assert walk.destination() is None


def test_a_walk_that_never_reached_the_offer_asks_for_nothing():
    walk = walk_genmed(gm__problem="weakness", gm__duration=5)
    assert walk.destination() is None


def test_the_offer_does_not_branch():
    """It is a preference, not triage: both answers lead to the same next
    question, so the clinical content of the intake is identical either way."""
    tree = get("general_medicine_routing")
    node = tree.node("gm.ayur")
    assert set(node.next) == {"default"}
    assert node.next["default"] == "gm.words"


def test_amending_the_answer_takes_the_destination_back():
    """The destination is derived from the answers like everything else — a
    patient who changes her mind on the read-back screen is not still routed."""
    walk = walk_genmed(gm__problem="weakness", gm__duration=5, gm__severity=4, gm__ayur="ayurveda")
    assert walk.destination() == "AYUR"
    walk.save("gm.ayur", "regular")
    assert walk.destination() is None


# -- 2. a red flag outranks it -------------------------------------------------


def test_a_red_flag_cancels_a_preference():
    """doc 24 §4. `gm.problem=breathing` is an urgent flag with no destination of
    its own; the patient stays on the staffed allopathic path she is already on,
    even though she asked for ayurveda two questions later."""
    walk = walk_genmed(gm__problem="breathing", gm__duration=1, gm__severity=9, gm__ayur="ayurveda")
    assert walk.red_flags(), "the breathlessness flag should have fired"
    assert walk.destination() is None


def test_two_flags_naming_two_departments_still_answer_the_same_way_every_time():
    """A patient can fire more than one rule, and two rules can name two clinics.

    The tie-break is the documented order — worst severity first, then flag id —
    and it is arbitrary between two *urgent* flags: here TB-suspect (PULM) and
    chest pain (GENMED) both fire and `ayr.chest_pain` sorts first. What matters
    clinically is that both flags are on the coordinator's strip and on the
    doctor's, that the patient is urgent, and that the answer does not depend on
    the order somebody typed the rules into the file. Where the *token* goes when
    two urgent destinations disagree is a human's call at the desk; see HANDOFF.
    """
    walk = Walk(get("ayurveda_respiratory"))
    walk.save("ayr.main", "khaansi")
    walk.save("ayr.days", 30)
    walk.save("ayr.sputum", "khoon")  # TB suspect -> PULM
    walk.save("ayr.fever", "roz")
    walk.save("ayr.weight", "ghata")
    walk.save("ayr.alarm", ["chest_pain"])  # urgent too, routes GENMED

    fired = {hit.id for hit in walk.red_flags()}
    assert {"ayr.tb_suspect", "ayr.chest_pain"} <= fired
    assert str(walk.priority()) == "urgent"
    assert walk.destination() == "GENMED"
    assert (
        walk.destination()
        == Walk.from_json(get("ayurveda_respiratory"), walk.to_json()).destination()
    )


def test_a_flag_with_a_destination_beats_a_worse_flag_without_one():
    """`route_to` is read down the fired list, not off the top of it. A flag that
    escalated the visit but named nowhere must not swallow one that named
    somewhere — the patient would be urgent in the wrong queue."""
    walk = Walk(get("ayurveda_routing"))
    walk.save("ay.concern", "pachan")
    walk.save("ay.pachan_kind", "jalan")
    walk.save("ay.duration", 20)
    walk.save("ay.severity", 6)
    walk.save("ay.appetite", "kam")
    walk.save("ay.bowel", "kabz")
    walk.save("ay.alarm", ["weight_loss"])
    assert {hit.id for hit in walk.red_flags()} == {"ay.route.weight_loss"}
    assert walk.destination() == "GENMED"


# -- 3. TB is notifiable (doc 24 §4) -------------------------------------------


def tb_walk(*, days: int, sputum: str, fever: str, weight: str) -> Walk:
    walk = Walk(get("ayurveda_respiratory"))
    walk.save("ayr.main", "khaansi")
    walk.save("ayr.days", days)
    walk.save("ayr.sputum", sputum)
    walk.save("ayr.fever", fever)
    walk.save("ayr.weight", weight)
    walk.save("ayr.alarm", ["none"])
    return walk


@pytest.mark.parametrize(
    ("days", "sputum", "fever", "weight"),
    [
        (2, "khoon", "nahin", "waisa"),  # blood in the sputum, on day two
        (21, "safed", "roz", "waisa"),  # three weeks + evening fever
        (14, "sookhi", "nahin", "ghata"),  # two weeks + weight loss
        (60, "peela", "kabhi", "ghata"),  # long cough + weight loss
    ],
)
def test_a_tb_suspect_answer_routes_to_the_chest_clinic(days, sputum, fever, weight):
    walk = tb_walk(days=days, sputum=sputum, fever=fever, weight=weight)
    assert "ayr.tb_suspect" in {hit.id for hit in walk.red_flags()}
    assert walk.destination() == "PULM"
    assert str(walk.priority()) == "urgent"


@pytest.mark.parametrize(
    ("days", "sputum", "fever", "weight"),
    [
        (3, "safed", "nahin", "waisa"),  # an ordinary short cough
        (30, "safed", "nahin", "waisa"),  # long, but neither fever nor weight loss
        (5, "peela", "roz", "ghata"),  # fever and weight loss, but under two weeks
    ],
)
def test_an_ordinary_cough_is_not_made_into_a_tb_suspect(days, sputum, fever, weight):
    """The other half of the rule. A flag that fires on everything is a flag
    nobody reads, and this one ends in a notifiable disease register."""
    walk = tb_walk(days=days, sputum=sputum, fever=fever, weight=weight)
    assert "ayr.tb_suspect" not in {hit.id for hit in walk.red_flags()}
    assert walk.destination() is None


def test_the_tb_instruction_says_adjunct_not_instead():
    """The words the patient actually hears (doc 02 §5 speaks `instruction`
    verbatim). It has to name the clinic and say the ayurveda visit does not
    replace it — that sentence is the whole clinical point of the rule."""
    spec = next(f for f in get("ayurveda_respiratory").red_flags if f.id == "ayr.tb_suspect")
    assert spec.route_to == "PULM"
    assert str(spec.severity) == "urgent"
    for lang in ("en", "hi", "mr", "te"):
        assert spec.instruction[lang].strip()
    assert "DOTS" in spec.instruction["en"]


# -- 4. an ayurveda tree never routes a red flag back into ayurveda ------------


def test_no_ayurveda_red_flag_routes_into_an_ayurveda_department():
    """doc 24 §4 — red flags stay allopathic. A rule that escalated a patient
    *into* the department she is already in would be an escalation that does
    nothing, dressed as one that does something."""
    for tree in load_bank().values():
        for spec in tree.red_flags:
            assert spec.route_to != "AYUR", f"{tree.key}: {spec.id} routes a red flag to AYUR"


def test_every_destination_in_the_bank_is_a_department_the_hospital_runs():
    for tree in load_bank().values():
        unknown = visibility.offered_departments(tree) - ALL_DEPARTMENTS
        assert not unknown, f"{tree.key} names {sorted(unknown)}"


# -- 5. a closed department is not offered ------------------------------------


def test_closing_ayurveda_removes_the_question_entirely():
    tree = get("general_medicine_routing")
    closed = visibility.for_active(tree, ALL_DEPARTMENTS - {"AYUR"})

    assert "gm.ayur" not in closed.nodes
    # and the question before it now leads where the offer used to lead, so the
    # patient walks the tree exactly as they did before doc 24.
    assert closed.node("gm.severity").next["default"] == "gm.words"


def test_pruning_leaves_a_tree_that_still_validates():
    """The prune produces content that will be asked to a patient, so it goes
    back through the same validator an authored file does. An orphaned branch or
    a rule left reading a node that is gone fails here, not on a kiosk."""
    for tree in load_bank().values():
        closed = visibility.for_active(tree, ALL_DEPARTMENTS - {"AYUR"})
        parse(closed.to_json())  # raises TreeError if pruning broke it


def test_an_open_department_keeps_its_offer_and_the_same_tree_object():
    tree = get("general_medicine_routing")
    assert visibility.for_active(tree, ALL_DEPARTMENTS) is tree


def test_a_tree_with_no_offers_is_untouched():
    tree = get("med_onc_new_patient")
    assert visibility.for_active(tree, set()) is tree


# -- 6. the authoring contract that makes pruning safe -------------------------


def _offer_tree(**node_overrides) -> dict:
    text = {"en": "x", "hi": "क्ष", "mr": "क्ष", "te": "క్ష"}
    node = {
        "id": "q.offer",
        "type": "single",
        "text": text,
        "options": [
            {"id": "yes", "text": text, "department": "AYUR"},
            {"id": "no", "text": text},
        ],
        "next": {"default": "q.end"},
    }
    node.update(node_overrides)
    return {
        "key": "offer_probe",
        "version": 1,
        "languages": ["en", "hi", "mr", "te"],
        "title": text,
        "root": "q.offer",
        "nodes": [node, {"id": "q.end", "type": "free_voice", "text": text}],
    }


def test_an_offer_node_is_accepted_in_its_intended_shape():
    parse(_offer_tree())


def test_an_offer_with_a_third_option_is_refused():
    """Removing the offer from a three-option question would leave a patient
    with a question that quietly lost a third of its answers."""
    text = {"en": "x", "hi": "क्ष", "mr": "क्ष", "te": "క్ష"}
    data = _offer_tree()
    data["nodes"][0]["options"].append({"id": "maybe", "text": text})
    with pytest.raises(TreeError, match="exactly two options"):
        parse(data)


def test_an_offer_that_branches_is_refused():
    with pytest.raises(TreeError, match="next.default only"):
        parse(_offer_tree(next={"yes": "q.end", "default": "q.end"}))


def test_a_multi_select_offer_is_refused():
    data = _offer_tree(type="multi")
    with pytest.raises(TreeError, match="single-choice"):
        parse(data)


def test_a_red_flag_may_not_read_an_offer_node():
    """It would stop firing the day the department closed — the exact silent
    failure the rule validator exists to prevent."""
    text = {"en": "x", "hi": "क्ष", "mr": "क्ष", "te": "క్ష"}
    data = _offer_tree()
    data["red_flags"] = [
        {
            "id": "probe.flag",
            "severity": "urgent",
            "when": {"node": "q.offer", "op": "eq", "value": "yes"},
            "label": text,
            "instruction": text,
        }
    ]
    with pytest.raises(TreeError, match="may not depend on one"):
        parse(data)


@pytest.mark.parametrize("code", ["ayur", "AY-UR", "1AYUR", "", 7])
def test_a_destination_must_look_like_a_department_code(code):
    data = _offer_tree()
    data["nodes"][0]["options"][0]["department"] = code
    with pytest.raises(TreeError, match="department code"):
        parse(data)


def test_the_canonical_form_carries_the_destination_and_round_trips():
    """The kiosk walks `to_json()` offline. A destination that survived parse but
    not serialisation would be a preference honoured online and dropped during an
    outage."""
    tree = get("general_medicine_routing")
    payload = tree.to_json()
    node = next(n for n in payload["nodes"] if n["id"] == "gm.ayur")
    assert [o["department"] for o in node["options"]] == ["AYUR", None]
    assert [f.route_to for f in parse(payload).red_flags] == [f.route_to for f in tree.red_flags]
    reparsed = parse(payload).node("gm.ayur")
    assert reparsed.option("ayurveda").department == "AYUR"
    assert reparsed.option("regular").department is None
