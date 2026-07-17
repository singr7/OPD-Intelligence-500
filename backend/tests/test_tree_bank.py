"""The authored pilot tree bank (doc 03 §3) — the clinical content itself.

`test_trees.py` proves the engine is right. This file proves the *content* is,
which is a different job: these eleven trees are what an actual patient in Alwar
is asked, and what an oncologist signs off in S21.

The checks worth understanding:

- **Every tree walks to an end.** A dead end is an intake that can never call
  `finish_and_summarize` — a patient stuck at a question with no way out.
- **Every red flag can actually fire.** A rule joining two nodes on branches that
  exclude each other is unsatisfiable, and an unsatisfiable red flag is the worst
  artefact in the system: it appears in the S21 review pack, gets signed off, and
  never fires.
- **doc 03 §1's starter flags exist and fire on a real scenario**, rather than
  merely parsing.

The Hindi here was authored by a model and has **not** been reviewed by a native
speaker or a clinician — see HANDOFF. These tests check that text is *present* and
structurally sound. They cannot check that it is good Hindi or good medicine.
"""

from __future__ import annotations

import json

import pytest

from app.models.enums import Lang, Priority
from app.seed import SEEDS_DIR
from app.trees import rules as rule_lang
from app.trees.bank import TREES_DIR, load_bank
from app.trees.schema import NodeType, Tree
from app.trees.walker import Walk

#: doc 03 §3's pilot bank, and doc 06's S4 line, spelled out.
CLINICAL_TREES = {
    "med_onc_new_patient": "MEDONC",
    "med_onc_between_cycle": "MEDONC",
    "med_onc_pain": "MEDONC",
    "rad_onc_review": "RADONC",
    "surg_onc_post_op": "SURGONC",
    "palliative_esas": "PALL",
}
ROUTING_TREES = {
    "general_medicine_routing": "GENMED",
    "gynae_routing": "GYNAE",
    "ent_routing": "ENT",
    "pulmonology_routing": "PULM",
    "dermatology_routing": "DERM",
}
PILOT_BANK = CLINICAL_TREES | ROUTING_TREES

#: S4 authors en+hi; mr and te are S13's.
PILOT_LANGUAGES = (Lang.EN, Lang.HI)


@pytest.fixture(scope="module")
def bank() -> dict[str, Tree]:
    return load_bank()


def tree_ids() -> list[str]:
    return sorted(PILOT_BANK)


def department_codes() -> set[str]:
    hospital = json.loads((SEEDS_DIR / "hospital.json").read_text())
    return {dept["code"] for dept in hospital["departments"]}


def descendants(tree: Tree, node_id: str) -> set[str]:
    """Every node reachable from `node_id`, following any branch."""
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        for target in tree.node(current).next.values():
            if target is not None and target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def can_co_occur(tree: Tree, left: str, right: str) -> bool:
    """Can both nodes be answered in one intake?

    Every node is reachable from the root (the validator guarantees it), so in a
    DAG two nodes share a path exactly when one reaches the other. If neither
    does, they sit on branches that exclude each other.
    """
    return left == right or right in descendants(tree, left) or left in descendants(tree, right)


def walk_to_the_end(tree: Tree, *, choose_last: bool = False) -> Walk:
    """Answer every question with its first (or last) option / lowest value."""
    walk = Walk(tree)
    guard = 0
    while (node := walk.current) is not None:
        guard += 1
        assert guard <= len(tree.nodes) + 1, f"{tree.ref}: {node.id} is not terminating"
        walk.save(node.id, _an_answer(node, choose_last=choose_last), text="test")
    return walk


def _an_answer(node, *, choose_last: bool):
    if node.type is NodeType.SINGLE:
        return node.options[-1].id if choose_last else node.options[0].id
    if node.type in (NodeType.MULTI, NodeType.BODY_MAP):
        return [node.options[-1].id if choose_last else node.options[0].id]
    if node.type in (NodeType.SCALE, NodeType.NUMBER):
        bound = node.max if choose_last else node.min
        return bound if bound is not None else 0
    return "kuch nahi"


# -- the bank is what doc 03 §3 and doc 06 asked for ---------------------------


def test_the_bank_holds_exactly_the_pilot_trees(bank):
    assert set(bank) == set(PILOT_BANK)


def test_every_authored_file_parses(bank):
    """`load_bank` parses through the validator, so this passing means every file
    on disk is a tree that is safe to ask."""
    assert len(bank) == len(list(TREES_DIR.glob("*.json"))) == 11


@pytest.mark.parametrize("key", tree_ids())
def test_each_tree_belongs_to_a_real_department(bank, key):
    assert bank[key].department == PILOT_BANK[key]
    assert bank[key].department in department_codes()


@pytest.mark.parametrize("key", tree_ids())
def test_each_tree_speaks_english_and_hindi(bank, key):
    """doc 06 S4: "in en+hi first (mr/te text in S13)". The validator already
    enforces that a declared language is *complete*; this pins which ones."""
    assert bank[key].languages == PILOT_LANGUAGES


@pytest.mark.parametrize("key", tree_ids())
def test_each_tree_ends_rather_than_trapping_the_patient(bank, key):
    """Both extremes of every branch must reach an end — a dead end is an intake
    that can never call finish_and_summarize."""
    for choose_last in (False, True):
        walk = walk_to_the_end(bank[key], choose_last=choose_last)
        assert walk.is_complete
        assert walk.current is None


@pytest.mark.parametrize("key", tree_ids())
def test_each_tree_asks_for_the_patients_own_words(bank, key):
    """doc 03 §4 prints one quote in the patient's own words. Every tree has to
    collect one, and every tree ends by asking for it."""
    tree = bank[key]
    free_voice = [node for node in tree.nodes.values() if node.type is NodeType.FREE_VOICE]
    assert free_voice, f"{tree.ref} never lets the patient speak freely"
    assert walk_to_the_end(tree).path()[-1] == free_voice[-1].id


@pytest.mark.parametrize("key", tree_ids())
def test_every_red_flag_in_the_bank_can_actually_fire(bank, key):
    """An `and` over two nodes on mutually exclusive branches never fires — and a
    flag that never fires is worse than no flag, because it reads as reviewed.

    Only `and`-rooted rules are checked: an `or` across branches is legitimate
    (either side can fire alone), and `unanswered` is *satisfied* by a node being
    off-path. Those cases need real satisfiability, not reachability — noted for
    S18's editor, which will need it when non-engineers author these.
    """
    tree = bank[key]
    for flag in tree.red_flags:
        if flag.when.get("op") != "and":
            continue
        nodes = sorted(rule_lang.referenced_nodes(flag.when))
        for left in nodes:
            for right in nodes:
                assert can_co_occur(tree, left, right), (
                    f"{tree.ref}: red flag {flag.id!r} needs {left!r} and {right!r} together, "
                    "but they are on branches that exclude each other — it can never fire"
                )


@pytest.mark.parametrize("key", tree_ids())
def test_every_red_flag_tells_the_patient_what_to_do(bank, key):
    """The instruction is spoken verbatim (doc 02 §5) — it is the actual thing a
    frightened patient hears, so it must say something in both languages."""
    for flag in bank[key].red_flags:
        for lang in PILOT_LANGUAGES:
            instruction = flag.instruction[str(lang)]
            assert len(instruction) > 10, f"{flag.id} has no instruction in {lang}"
            assert flag.label[str(lang)]
        assert flag.severity in (Priority.SEMI, Priority.URGENT)


@pytest.mark.parametrize("key", tree_ids())
def test_no_tree_can_raise_a_flag_before_anything_is_answered(bank, key):
    """Silence is not evidence — an intake that opens already urgent would send
    every patient to the front of the queue."""
    assert Walk(bank[key]).red_flags() == ()
    assert Walk(bank[key]).priority() is Priority.ROUTINE


# -- doc 03 §1's starter red flags, on real scenarios ---------------------------


def test_fever_after_chemo_is_urgent_but_fever_alone_is_not(bank):
    """doc 03 §1's first starter: "fever ≥38°C within 14 days of chemo". The
    canonical reason red flags are cross-node and deterministic."""
    tree = bank["med_onc_between_cycle"]

    recent = Walk(tree)
    recent.save("mo.cyc.days_since", 7)
    recent.save("mo.cyc.fever_temp", 38.5)
    assert "mo.cyc.febrile_neutropenia" in {flag.id for flag in recent.red_flags()}
    assert recent.priority() is Priority.URGENT

    late = Walk(tree)
    late.save("mo.cyc.days_since", 30)
    late.save("mo.cyc.fever_temp", 38.5)
    fired = {flag.id for flag in late.red_flags()}
    assert "mo.cyc.febrile_neutropenia" not in fired
    assert "mo.cyc.fever_late" in fired
    assert late.priority() is Priority.SEMI


def test_no_fever_after_recent_chemo_raises_nothing(bank):
    walk = Walk(bank["med_onc_between_cycle"])
    walk.save("mo.cyc.days_since", 3)
    walk.save("mo.cyc.fever_temp", 0)
    assert walk.red_flags() == ()


def test_the_febrile_neutropenia_boundary_is_exactly_38_and_14_days(bank):
    """Both thresholds are clinical, and both are off-by-one bait."""
    tree = bank["med_onc_between_cycle"]

    def fires(days: float, temp: float) -> bool:
        walk = Walk(tree)
        walk.save("mo.cyc.days_since", days)
        walk.save("mo.cyc.fever_temp", temp)
        return "mo.cyc.febrile_neutropenia" in {flag.id for flag in walk.red_flags()}

    assert fires(14, 38) is True
    assert fires(14, 37.9) is False
    assert fires(15, 38) is False


def test_active_bleeding_is_urgent(bank):
    """doc 03 §1 starter: active bleeding."""
    walk = Walk(bank["med_onc_new_patient"])
    walk.save("mo.new.diagnosis", "confirmed")
    walk.save("mo.new.reports", "yes")
    walk.save("mo.new.problems", ["pain"])
    walk.save("mo.new.duration", 10)
    walk.save("mo.new.weight_loss", "no")
    walk.save("mo.new.fever_temp", 0)
    walk.save("mo.new.chest", "no")
    walk.save("mo.new.bleeding", "yes")
    assert "mo.new.bleeding" in {flag.id for flag in walk.red_flags()}
    assert walk.priority() is Priority.URGENT


def test_chest_pain_or_breathlessness_is_urgent(bank):
    """doc 03 §1 starter: chest pain / breathlessness."""
    for answer in ("chest_pain", "breathless", "both"):
        walk = Walk(bank["med_onc_new_patient"])
        walk.save("mo.new.diagnosis", "confirmed")
        walk.save("mo.new.reports", "yes")
        walk.save("mo.new.problems", ["pain"])
        walk.save("mo.new.duration", 10)
        walk.save("mo.new.weight_loss", "no")
        walk.save("mo.new.fever_temp", 0)
        walk.save("mo.new.chest", answer)
        assert "mo.new.cardioresp" in {flag.id for flag in walk.red_flags()}, answer


def test_severe_vomiting_over_24h_is_urgent(bank):
    """doc 03 §1 starter: severe vomiting >24h."""
    walk = Walk(bank["med_onc_new_patient"])
    walk.save("mo.new.diagnosis", "confirmed")
    walk.save("mo.new.reports", "yes")
    walk.save("mo.new.problems", ["vomiting"])
    walk.save("mo.new.duration", 2)
    walk.save("mo.new.weight_loss", "no")
    walk.save("mo.new.fever_temp", 0)
    walk.save("mo.new.chest", "no")
    walk.save("mo.new.bleeding", "no")
    walk.save("mo.new.vomiting_24", "over_24")
    assert "mo.new.vomiting" in {flag.id for flag in walk.red_flags()}


def test_new_confusion_is_urgent(bank):
    """doc 03 §1 starter: new confusion."""
    walk = Walk(bank["med_onc_new_patient"])
    walk.save("mo.new.diagnosis", "confirmed")
    walk.save("mo.new.reports", "yes")
    walk.save("mo.new.problems", ["weakness"])
    walk.save("mo.new.duration", 2)
    walk.save("mo.new.weight_loss", "no")
    walk.save("mo.new.fever_temp", 0)
    walk.save("mo.new.chest", "no")
    walk.save("mo.new.bleeding", "no")
    walk.save("mo.new.vomiting_24", "no")
    walk.save("mo.new.confusion", "yes")
    assert "mo.new.confusion" in {flag.id for flag in walk.red_flags()}


# -- the trees branch the way their content implies ----------------------------


def test_the_radiation_site_decides_which_questions_follow(bank):
    """Site-specific review (doc 03 §3: "skin, swallowing, urinary per site"). A
    pelvis patient must never be asked about swallowing."""
    tree = bank["rad_onc_review"]

    head_neck = Walk(tree)
    head_neck.save("rt.site", "head_neck")
    assert head_neck.current.id == "rt.hn.swallow"

    pelvis = Walk(tree)
    pelvis.save("rt.site", "pelvis")
    assert pelvis.current.id == "rt.pelvis.urinary"
    assert "rt.hn.swallow" not in pelvis.path()

    breast = Walk(tree)
    breast.save("rt.site", "breast")
    assert breast.current.id == "rt.skin"


def test_changing_the_radiation_site_drops_the_wrong_sites_answers(bank):
    """The amendment case on real content: a patient who corrects 'head and neck'
    to 'pelvis' must not carry a swallowing answer into the doctor's summary."""
    walk = Walk(bank["rad_onc_review"])
    walk.save("rt.site", "head_neck")
    walk.save("rt.hn.swallow", "liquids_only")
    assert "rt.hn.swallow" in walk.answers

    walk.save("rt.site", "pelvis")
    assert "rt.hn.swallow" not in walk.answers
    assert walk.current.id == "rt.pelvis.urinary"


def test_the_pain_tree_maps_the_body_and_scales_severity(bank):
    walk = Walk(bank["med_onc_pain"])
    walk.save("pain.where", ["back", "legs"], text="peeth aur taang me dard")
    walk.save("pain.severity", 9)
    assert "pain.severe" in {flag.id for flag in walk.red_flags()}
    assert walk.priority() is Priority.URGENT


def test_the_esas_tree_flags_distress_without_a_model(bank):
    """ESAS asks about sadness and anxiety plainly; the flag is arithmetic, not
    a judgement the model makes."""
    walk = Walk(bank["palliative_esas"])
    walk.save("pall.pain", 2)
    walk.save("pall.tired", 4)
    walk.save("pall.nausea", 1)
    walk.save("pall.appetite", 3)
    walk.save("pall.breath", 1)
    walk.save("pall.drowsy", 2)
    walk.save("pall.low", 8)
    walk.save("pall.anxious", 3)
    assert "pall.distress" in {flag.id for flag in walk.red_flags()}
    assert walk.priority() is Priority.SEMI


def test_post_op_fever_with_a_clean_wound_is_not_the_same_as_with_a_leaking_one(bank):
    tree = bank["surg_onc_post_op"]

    infected = Walk(tree)
    infected.save("so.op.days_since", 6)
    infected.save("so.op.wound", "discharge")
    infected.save("so.op.fever_temp", 38.6)
    fired = {flag.id for flag in infected.red_flags()}
    assert "so.op.wound_infection" in fired
    assert infected.priority() is Priority.URGENT

    clean = Walk(tree)
    clean.save("so.op.days_since", 6)
    clean.save("so.op.wound", "clean")
    clean.save("so.op.fever_temp", 38.6)
    fired = {flag.id for flag in clean.red_flags()}
    assert fired == {"so.op.fever"}
    assert clean.priority() is Priority.SEMI


def test_the_routing_trees_stay_thin(bank):
    """doc 03 §3: "thinner trees for routing walk-ins". A walk-in being routed is
    not the moment for a twenty-question intake."""
    for key in ROUTING_TREES:
        assert len(bank[key].nodes) <= 6, f"{key} is no longer a thin routing tree"


def test_the_bank_covers_every_department_the_hospital_has(bank):
    """A department with no tree is a patient routed to a desk with nothing to
    ask them — the classifier can return any of these nine."""
    assert {tree.department for tree in bank.values()} == department_codes()
