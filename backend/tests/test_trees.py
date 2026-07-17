"""The question-tree engine (doc 03 §3): schema, validator, rules, walker.

Three things are being protected here.

**The validator's strictness (AC: "tree validator rejects malformed trees").**
Trees are edited by non-engineers in S18 and signed off by an oncologist in S21.
Every malformed tree that parses becomes a bad question asked to a real patient,
or — worse — a red flag that reads as reviewed and never fires. So the validator's
job is to make "it published" mean "it is safe to ask", and the tests below are
mostly a list of the specific ways authored JSON goes wrong.

**The walker's determinism (AC: "walker unit tests cover branching + red flags").**
The walker is the whole of tier V3 and the engine under S5's four tools. Position
is derived from the answers, so the same answers must always produce the same next
question — on any tier, after any failover.

**The red-flag boundary.** No model decides a flag. These tests pin that flags are
a pure function of the answers, recomputed rather than accumulated.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.models.enums import Lang, Priority
from app.trees import rules as rule_lang
from app.trees.schema import MAX_OPTIONS, NodeType, TreeError, parse
from app.trees.walker import Answer, AnswerError, Walk

# -- fixtures ------------------------------------------------------------------


def demo() -> dict[str, Any]:
    """A small tree that still has every shape that matters: a branch, a scale, a
    number, a multi, an option-level flag and a cross-node rule."""
    return {
        "key": "demo",
        "version": 1,
        "department": "MEDONC",
        "languages": ["en", "hi"],
        "title": {"en": "Demo", "hi": "डेमो"},
        "root": "site",
        "nodes": [
            {
                "id": "site",
                "type": "single",
                "text": {"en": "Where is the problem?", "hi": "समस्या कहाँ है?"},
                "options": [
                    {"id": "chest", "text": {"en": "Chest", "hi": "छाती"}},
                    {"id": "belly", "text": {"en": "Belly", "hi": "पेट"}},
                ],
                "next": {"chest": "chest.pain", "belly": "belly.symptoms", "default": "temp"},
            },
            {
                "id": "chest.pain",
                "type": "scale",
                "text": {"en": "How bad is the pain?", "hi": "दर्द कितना है?"},
                "min": 0,
                "max": 10,
                "next": {"default": "temp"},
            },
            {
                "id": "belly.symptoms",
                "type": "multi",
                "text": {"en": "What else?", "hi": "और क्या?"},
                "options": [
                    {"id": "vomiting", "text": {"en": "Vomiting", "hi": "उल्टी"}},
                    {"id": "blood", "text": {"en": "Blood", "hi": "खून"}, "flag": True},
                ],
                "next": {"default": "temp"},
                "red_flag": {
                    "id": "gi.bleed",
                    "severity": "urgent",
                    "label": {"en": "Bleeding", "hi": "रक्तस्राव"},
                    "instruction": {"en": "See a nurse now.", "hi": "अभी नर्स से मिलें।"},
                },
            },
            {
                "id": "temp",
                "type": "number",
                "text": {"en": "Your temperature?", "hi": "आपका तापमान?"},
                "min": 34,
                "max": 43,
                "unit": "C",
                "next": {"default": "chemo.recent"},
            },
            {
                "id": "chemo.recent",
                "type": "single",
                "text": {"en": "Chemo in the last 14 days?", "hi": "क्या 14 दिन में कीमो हुई?"},
                "options": [
                    {"id": "yes", "text": {"en": "Yes", "hi": "हाँ"}},
                    {"id": "no", "text": {"en": "No", "hi": "नहीं"}},
                ],
            },
        ],
        # The canonical cross-node flag from doc 03 §1: fever ≥38 within 14 days
        # of chemo. Neither node is alarming alone; the pair is an emergency.
        "red_flags": [
            {
                "id": "febrile.neutropenia",
                "severity": "urgent",
                "when": {
                    "op": "and",
                    "rules": [
                        {"node": "temp", "op": "gte", "value": 38},
                        {"node": "chemo.recent", "op": "eq", "value": "yes"},
                    ],
                },
                "label": {"en": "Fever after chemo", "hi": "कीमो के बाद बुखार"},
                "instruction": {"en": "Tell staff at once.", "hi": "तुरंत स्टाफ को बताएं।"},
            }
        ],
    }


def without(node_id: str, key: str, value: Any = ..., data: dict[str, Any] | None = None):
    """Return the demo tree with one node's key set (or removed if value is ...)."""
    tree = data or demo()
    node = next(n for n in tree["nodes"] if n["id"] == node_id)
    if value is ...:
        node.pop(key, None)
    else:
        node[key] = value
    return tree


def rejects(tree: dict[str, Any], *, because: str) -> str:
    with pytest.raises(TreeError) as caught:
        parse(tree)
    message = str(caught.value)
    assert because in message, f"expected {because!r} in {message!r}"
    return message


# -- the tree parses at all ----------------------------------------------------


def test_the_demo_tree_parses():
    tree = parse(demo())
    assert tree.ref == "demo@v1"
    assert tree.department == "MEDONC"
    assert tree.languages == (Lang.EN, Lang.HI)
    assert set(tree.nodes) == {"site", "chest.pain", "belly.symptoms", "temp", "chemo.recent"}


def test_node_text_and_audio_answer_in_the_patients_language():
    tree = parse(without("site", "audio", {"hi": "site_hi.mp3"}))
    node = tree.node("site")
    assert node.ask(Lang.HI) == "समस्या कहाँ है?"
    assert node.ask(Lang.EN) == "Where is the problem?"
    assert node.audio_clip(Lang.HI) == "site_hi.mp3"
    # No recording yet is normal until S7/S21 — TTS covers it.
    assert node.audio_clip(Lang.EN) is None


def test_a_tree_speaks_only_the_languages_it_declares():
    tree = parse(demo())
    assert tree.speaks(Lang.HI)
    assert not tree.speaks(Lang.TE)


def test_an_unknown_language_falls_back_to_english_rather_than_dropping_the_patient():
    node = parse(demo()).node("site")
    assert node.ask(Lang.TE) == "Where is the problem?"


# -- AC1: the validator rejects malformed trees --------------------------------


def test_rejects_a_non_object():
    rejects("nope", because="must be an object")  # type: ignore[arg-type]


def test_rejects_unknown_top_level_keys():
    tree = demo() | {"colour": "blue"}
    rejects(tree, because="unexpected tree keys")


def test_rejects_a_bad_key_or_version():
    rejects(demo() | {"key": "Med Onc!"}, because="tree key must match")
    rejects(demo() | {"version": 0}, because="version must be an integer >= 1")
    rejects(demo() | {"version": True}, because="version must be an integer >= 1")


def test_rejects_a_root_that_is_not_a_node():
    rejects(demo() | {"root": "nowhere"}, because="is not one of the tree's nodes")


def test_rejects_an_edge_to_a_node_that_does_not_exist():
    rejects(without("temp", "next", {"default": "ghost"}), because="unknown node 'ghost'")


def test_rejects_duplicate_node_ids():
    tree = demo()
    tree["nodes"].append(copy.deepcopy(tree["nodes"][0]))
    rejects(tree, because="duplicate node id 'site'")


def test_rejects_an_unreachable_node():
    """Dead content still reads as reviewed. An oncologist signing a tree must be
    signing the questions patients are actually asked."""
    tree = demo()
    tree["nodes"].append(
        {
            "id": "orphan",
            "type": "single",
            "text": {"en": "Nobody asks this", "hi": "कोई नहीं पूछता"},
            "options": [{"id": "a", "text": {"en": "A", "hi": "अ"}}],
        }
    )
    rejects(tree, because="unreachable from root")


def test_rejects_a_cycle():
    tree = without("chemo.recent", "next", {"default": "site"})
    rejects(tree, because="cycle in the tree")


def test_rejects_a_node_pointing_at_itself():
    rejects(without("temp", "next", {"default": "temp"}), because="points at itself")


def test_rejects_missing_text_in_a_declared_language():
    """doc 07 §4 gate: every patient-facing string in all active languages."""
    rejects(without("site", "text", {"en": "Where?"}), because="missing text for ['hi']")


def test_rejects_text_in_a_language_the_tree_does_not_declare():
    """Translating into a language the tree does not serve means someone believes
    Telugu is live when it is not (S13)."""
    tree = without("site", "text", {"en": "Where?", "hi": "कहाँ?", "te": "ఎక్కడ?"})
    rejects(tree, because="text for ['te']")


def test_rejects_empty_text():
    rejects(without("site", "text", {"en": "  ", "hi": "कहाँ?"}), because="en text is empty")


def test_rejects_a_tree_without_english():
    tree = demo() | {"languages": ["hi"]}
    rejects(tree, because="must include 'en'")


def test_rejects_a_duplicate_or_unknown_language():
    rejects(demo() | {"languages": ["en", "en"]}, because="listed twice")
    rejects(demo() | {"languages": ["en", "fr"]}, because="unknown language")


def test_rejects_an_unknown_node_type():
    rejects(without("site", "type", "dropdown"), because="unknown node type")


def test_rejects_an_option_list_longer_than_the_kiosk_law():
    """doc 03 §1a — max 3–5 options/screen."""
    options = [
        {"id": f"o{index}", "text": {"en": f"Option {index}", "hi": f"विकल्प {index}"}}
        for index in range(MAX_OPTIONS + 1)
    ]
    message = rejects(without("site", "options", options), because="exceeds the")
    assert "doc 03 §1a" in message


def test_body_map_is_exempt_from_the_option_limit():
    """A torso has more than five places to hurt; it is a picture, not buttons."""
    tree = {
        "key": "body",
        "version": 1,
        "languages": ["en", "hi"],
        "title": {"en": "Body", "hi": "शरीर"},
        "root": "where",
        "nodes": [
            {
                "id": "where",
                "type": "body_map",
                "text": {"en": "Where does it hurt?", "hi": "कहाँ दर्द है?"},
                "options": [
                    {"id": f"r{index}", "text": {"en": f"Region {index}", "hi": f"क्षेत्र {index}"}}
                    for index in range(9)
                ],
            }
        ],
    }
    assert len(parse(tree).node("where").options) == 9


def test_rejects_a_choice_node_with_no_options():
    rejects(without("site", "options", ...), because="needs a non-empty 'options' list")


def test_rejects_options_on_a_node_that_cannot_have_them():
    rejects(
        without("temp", "options", [{"id": "a", "text": {"en": "A", "hi": "अ"}}]),
        because="takes no options",
    )


def test_rejects_duplicate_option_ids():
    options = [
        {"id": "chest", "text": {"en": "Chest", "hi": "छाती"}},
        {"id": "chest", "text": {"en": "Chest again", "hi": "छाती फिर"}},
    ]
    rejects(without("site", "options", options), because="duplicate option id")


def test_rejects_a_scale_without_bounds():
    """`6` means nothing later without knowing it was out of 10 — and the doctor
    screen renders it as severity."""
    rejects(without("chest.pain", "max", ...), because="needs both min and max")


def test_rejects_an_inverted_range():
    rejects(
        without("chest.pain", "min", 10, data=without("chest.pain", "max", 0)),
        because="must be less than",
    )


def test_rejects_min_max_on_a_node_without_a_range():
    rejects(without("site", "min", 1), because="only apply to scale and number")


def test_rejects_a_next_key_that_is_not_an_option_or_default():
    rejects(
        without("site", "next", {"elbow": "temp"}), because="is neither 'default' nor an option"
    )


def test_rejects_branching_on_a_list_answer():
    """A multi-select answer names two branches at once; picking one quietly is
    the kind of non-determinism that makes an intake unreproducible."""
    tree = without("belly.symptoms", "next", {"vomiting": "temp", "default": "temp"})
    rejects(tree, because="cannot pick a branch")


# -- AC1: red-flag authoring ---------------------------------------------------


def test_rejects_a_flag_raiser_with_no_flag_block():
    tree = without("temp", "red_flag_if", {"node": "temp", "op": "gte", "value": 38})
    rejects(tree, because="has no 'red_flag' block")


def test_rejects_a_flag_block_that_nothing_raises():
    tree = without(
        "temp",
        "red_flag",
        {
            "id": "orphan.flag",
            "severity": "urgent",
            "label": {"en": "X", "hi": "क्ष"},
            "instruction": {"en": "X", "hi": "क्ष"},
        },
    )
    rejects(tree, because="nothing raises it")


def test_rejects_a_routine_red_flag():
    """doc 03 §1: a red flag sets priority=urgent and alerts a nurse. A 'routine'
    one would parse and then quietly do nothing."""
    tree = demo()
    tree["red_flags"][0]["severity"] = "routine"
    rejects(tree, because="is not a red flag")


def test_rejects_a_flag_without_words_for_the_patient():
    tree = demo()
    del tree["red_flags"][0]["instruction"]
    rejects(tree, because="instruction")


def test_rejects_a_flag_instruction_missing_a_language():
    tree = demo()
    tree["red_flags"][0]["instruction"] = {"en": "Tell staff."}
    rejects(tree, because="missing text for ['hi']")


def test_rejects_duplicate_flag_ids():
    tree = demo()
    tree["red_flags"].append(copy.deepcopy(tree["red_flags"][0]))
    rejects(tree, because="duplicate red flag id")


def test_rejects_a_rule_against_an_unknown_node():
    tree = demo()
    tree["red_flags"][0]["when"] = {"node": "ghost", "op": "answered"}
    rejects(tree, because="unknown node 'ghost'")


def test_rejects_a_rule_whose_op_cannot_apply_to_its_node():
    """`contains` against a single-select silently never fires — and a flag that
    never fires reads as reviewed and safe."""
    tree = demo()
    tree["red_flags"][0]["when"] = {"node": "site", "op": "contains", "value": "chest"}
    rejects(tree, because="cannot apply to node 'site'")


def test_rejects_a_rule_against_free_voice():
    """Matching ASR text would make the flag depend on the transcriber, and would
    fire "no blood in my stool" as bleeding."""
    tree = demo()
    tree["nodes"].append(
        {
            "id": "words",
            "type": "free_voice",
            "text": {"en": "Anything else?", "hi": "और कुछ?"},
        }
    )
    tree = without("chemo.recent", "next", {"default": "words"}, data=tree)
    tree["red_flags"][0]["when"] = {"node": "words", "op": "eq", "value": "blood"}
    message = rejects(tree, because="cannot apply to node 'words'")
    assert "transcriber" in message


def test_rejects_a_numeric_op_with_a_non_numeric_value():
    tree = demo()
    tree["red_flags"][0]["when"] = {"node": "temp", "op": "gte", "value": "hot"}
    rejects(tree, because="needs a numeric 'value'")


def test_rejects_a_nullary_op_carrying_a_value():
    tree = demo()
    tree["red_flags"][0]["when"] = {"node": "temp", "op": "answered", "value": 1}
    rejects(tree, because="takes no 'value'")


def test_rejects_a_malformed_group():
    tree = demo()
    tree["red_flags"][0]["when"] = {"op": "and", "rules": []}
    rejects(tree, because="non-empty 'rules'")

    tree["red_flags"][0]["when"] = {"op": "not", "rules": [{"node": "temp", "op": "answered"}] * 2}
    rejects(tree, because="'not' takes exactly one rule")


def test_option_level_flag_becomes_a_real_rule():
    """`flag: true` in doc 03 §3 is sugar, not decoration — it compiles to a rule."""
    tree = parse(demo())
    flag = next(f for f in tree.red_flags if f.id == "gi.bleed")
    assert flag.source_node == "belly.symptoms"
    assert rule_lang.referenced_nodes(flag.when) == {"belly.symptoms"}


# -- AC2: the walker branches --------------------------------------------------


def test_the_walk_starts_at_the_root():
    walk = Walk(parse(demo()))
    assert walk.current is not None and walk.current.id == "site"
    assert not walk.is_complete


def test_an_answer_selects_its_branch():
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    assert walk.current.id == "chest.pain"

    other = Walk(parse(demo()))
    other.save("site", "belly")
    assert other.current.id == "belly.symptoms"


def test_the_walk_runs_to_completion_and_reports_its_path():
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    walk.save("chest.pain", 7)
    walk.save("temp", 37.2)
    walk.save("chemo.recent", "no")
    assert walk.is_complete
    assert walk.current is None
    assert walk.path() == ("site", "chest.pain", "temp", "chemo.recent")


def test_a_node_with_no_next_ends_the_tree():
    walk = Walk(parse(demo()))
    walk.save("site", "belly")
    walk.save("belly.symptoms", ["vomiting"])
    walk.save("temp", 36.5)
    walk.save("chemo.recent", "yes")
    assert walk.is_complete


def test_position_is_derived_so_the_same_answers_always_give_the_same_question():
    """The property the whole tier ladder rests on (doc 02 §5): V1 dies, S5 rebuilds
    the walk on V2 from the stored answers, and the patient is asked the same next
    question rather than repeating themselves."""
    first = Walk(parse(demo()))
    first.save("site", "chest")
    first.save("chest.pain", 4)

    rebuilt = Walk.from_json(parse(demo()), first.to_json())
    assert rebuilt.current.id == first.current.id == "temp"
    assert rebuilt.path() == first.path()


def test_answering_a_question_that_is_not_being_asked_is_refused():
    """A tree that can be answered out of order is one whose branch conditions were
    never really asked."""
    walk = Walk(parse(demo()))
    with pytest.raises(AnswerError, match="not the current question"):
        walk.save("temp", 39)


def test_amending_an_answer_reroutes_and_drops_the_abandoned_branch():
    """doc 03 §1: "I want to change something". The chest score was given on a
    branch the patient is no longer on — it must not reach the doctor's summary."""
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    walk.save("chest.pain", 9)
    assert "chest.pain" in walk.answers

    walk.save("site", "belly")
    assert "chest.pain" not in walk.answers
    assert walk.current.id == "belly.symptoms"


def test_amending_keeps_answers_that_are_still_on_the_path():
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    walk.save("chest.pain", 3)
    walk.save("temp", 38.5)
    walk.save("chest.pain", 8)  # amend a score, same branch
    assert walk.answers["temp"].value == 38.5
    assert walk.answers["chest.pain"].value == 8


# -- AC2: the walker flags -----------------------------------------------------


def test_no_flags_when_nothing_alarming_was_said():
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    walk.save("chest.pain", 2)
    walk.save("temp", 36.8)
    walk.save("chemo.recent", "no")
    assert walk.red_flags() == ()
    assert walk.priority() is Priority.ROUTINE


def test_a_cross_node_rule_fires_only_when_both_answers_are_present():
    """Fever alone is not febrile neutropenia; fever after chemo is."""
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    walk.save("chest.pain", 1)
    walk.save("temp", 38.4)
    assert walk.red_flags() == ()  # chemo question not answered yet

    walk.save("chemo.recent", "yes")
    flags = walk.red_flags()
    assert [flag.id for flag in flags] == ["febrile.neutropenia"]
    assert walk.priority() is Priority.URGENT


def test_a_flag_speaks_the_patients_language_verbatim():
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    walk.save("chest.pain", 1)
    walk.save("temp", 39)
    walk.save("chemo.recent", "yes")
    flag = walk.red_flags()[0]
    assert flag.say(Lang.HI) == "तुरंत स्टाफ को बताएं।"
    assert flag.name(Lang.EN) == "Fever after chemo"


def test_an_option_marked_flag_raises_its_nodes_flag():
    walk = Walk(parse(demo()))
    walk.save("site", "belly")
    walk.save("belly.symptoms", ["vomiting", "blood"])
    assert [flag.id for flag in walk.red_flags()] == ["gi.bleed"]


def test_flags_are_recomputed_not_accumulated():
    """An amendment that removes the alarming answer removes the flag. A flag that
    outlived its evidence would jump the queue for something already corrected."""
    walk = Walk(parse(demo()))
    walk.save("site", "belly")
    walk.save("belly.symptoms", ["blood"])
    assert walk.red_flags()

    walk.save("belly.symptoms", ["vomiting"])
    assert walk.red_flags() == ()


def test_flags_are_ordered_worst_first():
    tree = demo()
    tree["red_flags"].append(
        {
            "id": "a.milder",
            "severity": "semi",
            "when": {"node": "temp", "op": "gte", "value": 37.5},
            "label": {"en": "Warm", "hi": "गर्म"},
            "instruction": {"en": "Mention it.", "hi": "बताएं।"},
        }
    )
    walk = Walk(parse(tree))
    walk.save("site", "chest")
    walk.save("chest.pain", 1)
    walk.save("temp", 39)
    walk.save("chemo.recent", "yes")
    # Sorted by severity even though "a.milder" sorts first alphabetically.
    assert [flag.id for flag in walk.red_flags()] == ["febrile.neutropenia", "a.milder"]
    assert walk.priority() is Priority.URGENT


def test_a_hangup_raises_only_the_flags_its_answers_support():
    """S5 partial-saves a dropped call; silence must not invent urgency."""
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    assert walk.red_flags() == ()


# -- answer validation ---------------------------------------------------------


def test_rejects_an_option_that_is_not_on_the_node():
    walk = Walk(parse(demo()))
    with pytest.raises(AnswerError, match="is not an option"):
        walk.save("site", "elbow")


def test_rejects_the_wrong_shape_for_the_node_type():
    walk = Walk(parse(demo()))
    with pytest.raises(AnswerError, match="expected one option id"):
        walk.save("site", ["chest"])

    walk.save("site", "belly")
    with pytest.raises(AnswerError, match="expected a list"):
        walk.save("belly.symptoms", "vomiting")


def test_rejects_a_number_outside_the_nodes_range():
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    with pytest.raises(AnswerError, match="above the maximum"):
        walk.save("chest.pain", 11)
    with pytest.raises(AnswerError, match="below the minimum"):
        walk.save("chest.pain", -1)


def test_rejects_a_bool_as_a_number():
    """`True` is not a 1°C fever."""
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    with pytest.raises(AnswerError, match="expected a number"):
        walk.save("chest.pain", True)


def test_rejects_no_answer_at_all():
    walk = Walk(parse(demo()))
    with pytest.raises(AnswerError, match="an answer is required"):
        walk.save("site", None)


def test_normalises_so_the_jsonb_does_not_depend_on_the_channel():
    """A kiosk tap and a model's function call must write the same JSONB."""
    walk = Walk(parse(demo()))
    walk.save("site", "chest")
    walk.save("chest.pain", 7.0)
    assert walk.answers["chest.pain"].value == 7
    walk.save("temp", 38.5)
    assert walk.answers["temp"].value == 38.5


def test_multi_select_dedupes_and_keeps_order():
    walk = Walk(parse(demo()))
    walk.save("site", "belly")
    walk.save("belly.symptoms", ["blood", "vomiting", "blood"])
    assert walk.answers["belly.symptoms"].value == ["blood", "vomiting"]


# -- the answers JSONB contract ------------------------------------------------


def test_the_answer_shape_matches_intake_answers():
    """doc 03 §1 AC: the same answers JSONB from every tier and channel.
    `Intake.answers` is {node_id: {value, text, text_en, at}}."""
    walk = Walk(parse(demo()))
    walk.save("site", "chest", text="seene me dard", lang=Lang.HI)
    row = walk.to_json()["site"]
    assert row["value"] == "chest"
    assert row["text"] == "seene me dard"
    assert row["lang"] == "hi"
    assert row["text_en"] is None
    assert row["at"].endswith("+00:00")


def test_the_patients_own_words_survive_the_mapping():
    """doc 03 §4 prints the quote, and a mis-mapped option is only recoverable if
    what was actually said survived."""
    walk = Walk(parse(demo()))
    walk.save("site", "chest", text="mere seene me bahut dard hai")
    assert walk.answers["site"].text == "mere seene me bahut dard hai"


def test_a_walk_round_trips_through_json():
    walk = Walk(parse(demo()))
    walk.save("site", "belly", text="pet", lang=Lang.HI)
    walk.save("belly.symptoms", ["blood"])

    rebuilt = Walk.from_json(parse(demo()), walk.to_json())
    assert rebuilt.to_json() == walk.to_json()
    assert [flag.id for flag in rebuilt.red_flags()] == ["gi.bleed"]
    assert rebuilt.answers["site"].lang is Lang.HI


def test_from_json_drops_answers_for_nodes_the_tree_no_longer_has():
    """S18 publishes a new tree version without a deploy; a patient mid-answer is
    not the person to punish for it."""
    stored = {
        "site": {"value": "chest", "at": "2026-07-15T10:00:00+00:00"},
        "removed.node": {"value": "x", "at": "2026-07-15T10:00:00+00:00"},
    }
    walk = Walk.from_json(parse(demo()), stored)
    assert set(walk.answers) == {"site"}
    assert walk.current.id == "chest.pain"


def test_from_json_survives_a_junk_timestamp():
    walk = Walk.from_json(parse(demo()), {"site": {"value": "chest", "at": "not-a-date"}})
    assert walk.answers["site"].at is not None


def test_red_flag_hit_serialises_for_the_intake_row():
    walk = Walk(parse(demo()))
    walk.save("site", "belly")
    walk.save("belly.symptoms", ["blood"])
    row = walk.red_flags()[0].to_json()
    assert row["id"] == "gi.bleed"
    assert row["severity"] == "urgent"
    assert row["source_node"] == "belly.symptoms"


# -- the rule evaluator, directly ----------------------------------------------


def test_evaluate_is_total_and_never_raises_on_a_live_intake():
    """A bad rule must not 500 on a patient mid-sentence; `validate` is where it
    fails loudly, at seed and publish time."""
    assert rule_lang.evaluate({"op": "nonsense", "rules": []}, {}) is False
    assert rule_lang.evaluate({"node": "x", "op": "gte", "value": 1}, {"x": "hot"}) is False
    assert rule_lang.evaluate({}, {"x": 1}) is False


@pytest.mark.parametrize(
    ("rule", "values", "expected"),
    [
        ({"node": "n", "op": "eq", "value": "a"}, {"n": "a"}, True),
        ({"node": "n", "op": "ne", "value": "a"}, {"n": "b"}, True),
        ({"node": "n", "op": "gt", "value": 5}, {"n": 6}, True),
        ({"node": "n", "op": "gt", "value": 5}, {"n": 5}, False),
        ({"node": "n", "op": "gte", "value": 5}, {"n": 5}, True),
        ({"node": "n", "op": "lt", "value": 5}, {"n": 4}, True),
        ({"node": "n", "op": "lte", "value": 5}, {"n": 5}, True),
        ({"node": "n", "op": "in", "value": ["a", "b"]}, {"n": "b"}, True),
        ({"node": "n", "op": "contains", "value": "a"}, {"n": ["a", "b"]}, True),
        ({"node": "n", "op": "contains", "value": "z"}, {"n": ["a"]}, False),
        ({"node": "n", "op": "answered"}, {"n": 0}, True),
        ({"node": "n", "op": "answered"}, {}, False),
        ({"node": "n", "op": "unanswered"}, {}, True),
        # Silence is not evidence.
        ({"node": "n", "op": "eq", "value": "a"}, {}, False),
        ({"node": "n", "op": "ne", "value": "a"}, {}, False),
        ({"node": "n", "op": "eq", "value": "a"}, {"n": None}, False),
    ],
)
def test_leaf_ops(rule, values, expected):
    assert rule_lang.evaluate(rule, values) is expected


def test_groups_compose():
    values = {"a": 39, "b": "yes"}
    both = {
        "op": "and",
        "rules": [
            {"node": "a", "op": "gte", "value": 38},
            {"node": "b", "op": "eq", "value": "yes"},
        ],
    }
    assert rule_lang.evaluate(both, values) is True
    assert rule_lang.evaluate({"op": "not", "rules": [both]}, values) is False
    either = {
        "op": "or",
        "rules": [
            {"node": "a", "op": "lt", "value": 30},
            {"node": "b", "op": "eq", "value": "yes"},
        ],
    }
    assert rule_lang.evaluate(either, values) is True


def test_referenced_nodes_finds_every_node_a_rule_reads():
    rule = {
        "op": "and",
        "rules": [
            {"node": "a", "op": "answered"},
            {
                "op": "or",
                "rules": [{"node": "b", "op": "answered"}, {"node": "c", "op": "answered"}],
            },
        ],
    }
    assert rule_lang.referenced_nodes(rule) == {"a", "b", "c"}


def test_a_bool_never_counts_as_a_number():
    assert rule_lang.evaluate({"node": "n", "op": "gte", "value": 1}, {"n": True}) is False
    assert rule_lang.evaluate({"node": "n", "op": "eq", "value": 1}, {"n": True}) is False


# -- node helpers --------------------------------------------------------------


def test_node_type_shape_helpers():
    assert NodeType.SINGLE.wants_options and not NodeType.SINGLE.wants_range
    assert NodeType.SCALE.wants_range and not NodeType.SCALE.wants_options
    assert NodeType.BODY_MAP.wants_options


def test_asking_for_an_unknown_node_is_loud():
    tree = parse(demo())
    with pytest.raises(TreeError, match="no node"):
        tree.node("ghost")


def test_answer_from_json_tolerates_a_missing_lang():
    answer = Answer.from_json("site", {"value": "chest", "at": "2026-07-15T10:00:00+00:00"})
    assert answer.lang is None
    assert answer.value == "chest"


# -- the canonical form (S7: the offline kiosk's wire shape) --------------------


def test_the_canonical_form_is_a_fixed_point():
    """`parse(t.to_json()).to_json() == t.to_json()` — canonicalising is idempotent.

    This is the property the offline kiosk (S7) rests on. The TS walker is handed
    `to_json`, so the canonical form must carry the tree's whole *meaning*; if it
    ever lost or re-interpreted something, this fails here rather than on a kiosk
    during an outage.

    It is deliberately not `parse(t.to_json()) == parse(authored)`: the authored
    sugar (`flag: true`) is *consumed* by `parse` and does not survive into the
    canonical form — that is the point of desugaring, and `Option.flag` is
    therefore False on the reparse. What must survive is everything the walker
    reads, which is exactly what `to_json` emits.
    """
    canonical = parse(demo()).to_json()

    assert parse(canonical).to_json() == canonical


def test_the_canonical_form_is_json_serialisable():
    # The bundle is shipped over HTTP and stored in IndexedDB: nested Mappings
    # from the rule expressions must come out as plain containers.
    payload = json.dumps(parse(demo()).to_json())
    assert json.loads(payload)["key"] == "demo"


def test_the_canonical_form_desugars_flags_so_a_client_never_has_to():
    """Option-level `flag: true` and node-level `red_flag` arrive as real rules."""
    canonical = parse(demo()).to_json()

    flag_ids = {flag["id"] for flag in canonical["red_flags"]}
    assert flag_ids == {"gi.bleed", "febrile.neutropenia"}

    bleed = next(f for f in canonical["red_flags"] if f["id"] == "gi.bleed")
    assert bleed["source_node"] == "belly.symptoms"
    # The sugar is gone: a client reading `options` sees no `flag` key to
    # (mis)interpret, because flags live in one place only.
    node = next(n for n in canonical["nodes"] if n["id"] == "belly.symptoms")
    assert all("flag" not in option for option in node["options"])


def test_every_seeded_tree_is_a_fixed_point():
    """Not just the demo — the 11 authored trees are what actually ship offline."""
    paths = sorted((Path(__file__).resolve().parents[2] / "seeds" / "trees").glob("*.json"))
    assert paths, "no seeded trees found — the fixed-point check would vacuously pass"

    for path in paths:
        canonical = parse(json.loads(path.read_text())).to_json()
        assert parse(canonical).to_json() == canonical, f"{path.name} is not a fixed point"


def test_source_node_cannot_be_authored_on_node_level_sugar():
    """`source_node` is the parser's stamp, not an author's claim."""
    tree = demo()
    node = next(n for n in tree["nodes"] if n["id"] == "belly.symptoms")
    node["red_flag"]["source_node"] = "temp"
    rejects(tree, because="unexpected red_flag keys")


def test_a_tree_level_flag_cannot_claim_a_source_node_that_does_not_exist():
    tree = demo()
    tree["red_flags"][0]["source_node"] = "ghost"
    rejects(tree, because="is not a node of this tree")
