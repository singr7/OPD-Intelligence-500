"""Trees as a *closed* hospital sees them (doc 24 §5).

A tree may offer another department — "मैं आयुर्वेद इलाज के लिए आया/आई हूँ", an
option carrying `department: "AYUR"`. Doc 24 §5 is explicit that such an option
must not be rendered while its destination is closed, and that the fix is a
filter over the department list the backend already serves rather than a second
kind of tree.

This module is that filter, and it runs **server-side, once, on the canonical
tree** — in `app.trees.store.resolve_tree` (the live intake path) and in
`GET /kiosk/bundle` (the offline pack). Two consequences that were the point of
putting it there:

- The offline kiosk needs no filtering logic at all. It never receives the
  question, so it cannot render it, cache it, or walk a patient into a closed
  department during an outage — and the bundle's ETag already covers the
  department list, so opening Ayurveda invalidates yesterday's cached pack.
- WhatsApp and telephony inherit it, because they resolve trees through the same
  seam. A filter written into the kiosk's renderer would have left the bot
  offering a shut department to whoever texted in.

## Removing a question, not an answer

The whole node goes, not just the option. `schema._validate_offers` is what makes
that safe: an offering node is single-choice, has exactly two options (the offer
and carrying on as usual), branches only on `default`, and is read by no red
flag. So its parents are rewired to its `next.default` and nothing else in the
tree can tell the difference — no orphaned branch, no rule left reading a node
that is gone.

The result is re-`parse`d rather than trusted. Pruning produces a tree that will
be asked to a patient; it goes through the same validator as an authored one, and
if a future edit makes pruning produce something unreachable or cyclic, the test
suite says so instead of a kiosk saying it at 9am.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.trees.schema import Tree, parse


def offered_departments(tree: Tree) -> set[str]:
    """Every department code this tree can hand a patient to.

    Both halves of `Walk.destination`: the preferences an option expresses and
    the destinations a red flag names. Used by the tests that keep the tree bank
    honest about which departments it references.
    """
    codes = {option.department for node in tree.nodes.values() for option in node.options}
    codes |= {flag.route_to for flag in tree.red_flags}
    return {code for code in codes if code}


def for_active(tree: Tree, active: Iterable[str]) -> Tree:
    """`tree` with every offer of a closed department taken out.

    Returns the tree unchanged (the same object) when nothing needs removing,
    which is the common case for every tree in the bank and keeps the identity
    the `@cache`d bank hands out intact.

    `route_to` on a red flag is deliberately *not* pruned here. It is not a
    question a patient can be asked and it is not rendered anywhere — it is a
    destination the rule engine names when something clinical fires, and doc 24
    §4 makes that escalation the tree's most load-bearing content. A closed
    destination is handled where the destination is applied (a department that
    cannot be resolved leaves the visit where it is), not by quietly deleting
    the rule.
    """
    open_codes = set(active)
    doomed = [
        node.id
        for node in tree.nodes.values()
        if any(option.department and option.department not in open_codes for option in node.options)
    ]
    if not doomed:
        return tree

    data: dict[str, Any] = tree.to_json()
    root = data["root"]
    # `next.default` of each removed node — what its parents inherit. Resolved
    # transitively so two offering nodes in a row collapse correctly.
    replacement: dict[str, str | None] = {
        node.id: node.next.get("default") for node in tree.nodes.values() if node.id in set(doomed)
    }

    def resolve(node_id: str | None) -> str | None:
        seen: set[str] = set()
        while node_id in replacement and node_id not in seen:
            seen.add(node_id)
            node_id = replacement[node_id]
        return node_id

    data["nodes"] = [node for node in data["nodes"] if node["id"] not in replacement]
    for node in data["nodes"]:
        node["next"] = {edge: resolve(target) for edge, target in node["next"].items()}
    data["root"] = resolve(root)
    if data["root"] is None:
        # A tree whose every question offered a closed department. Not
        # representable today (the root of each routing tree is a clinical
        # question), and a tree with no questions is not something to serve a
        # patient in silence.
        raise ValueError(f"tree {tree.ref}: pruning left no questions to ask")
    return parse(data)
