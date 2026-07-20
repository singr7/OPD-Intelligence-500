"""Printable downtime paper sheets (doc 01 §5 pt 3, doc 03 §6).

Total blackout — power and network both gone — is the last tier of the Downtime
Protocol: "laminated paper intake sheets printed from the same question trees
(one per department, in 4 languages) ... staff use the next numbers from the
printed daily token block sheet." Two sheets are generated here, and both are
**generated from live data** so they never drift from the running system:

* **Intake sheets** — one fillable form per department, rendered straight from
  the department's question tree (`app.trees.bank`). Every node becomes a
  question with tick-boxes / blanks in the requested languages, so a coordinator
  batch-enters it later via the paper-entry screen against the same tree.
* **Token block sheet** — a grid of the big token numerals a kiosk's offline
  block covers (`app.offline`), to tear off and hand out by number during a
  blackout, from the same pre-allocated ranges the online allocator can never
  reach (so a paper token can't later collide on the record).

## Why HTML, not a server-rendered PDF

The sheets must render Devanagari (and later Telugu) correctly. Embedding Indic
fonts and doing the shaping inside a hand-rolled PDF is fragile; a real HTML→PDF
engine (WeasyPrint/pango) needs native libraries we don't ship. So these return
**print-optimised HTML** (A4 `@page`, tear guides, tick-boxes) that the browser
turns into a PDF via its print dialog — the same "browser fallback" stance the
ESC/POS token-slip bridge already takes (S7). A server-side PDF with embedded
Indic fonts is a deploy-time dependency decision (backlog, S19/S21).
"""

from __future__ import annotations

from html import escape
from typing import Any

from app.models.enums import Lang

#: The four pilot languages, in the order they print. mr/te text lands in trees
#: at S13; until then those columns fall back to English within `_text`.
DEFAULT_LANGS: tuple[Lang, ...] = (Lang.HI, Lang.EN)

# A tick-box glyph pair reused across question types.
_BOX = "☐"


def _text(mapping: dict[str, Any] | None, lang: Lang) -> str:
    """One language's string from a `{lang: text}` map, English as the floor."""
    if not mapping:
        return ""
    return mapping.get(str(lang)) or mapping.get("en") or ""


def _langs(requested: list[Lang] | tuple[Lang, ...] | None) -> tuple[Lang, ...]:
    if not requested:
        return DEFAULT_LANGS
    # De-dupe while keeping order.
    seen: list[Lang] = []
    for lang in requested:
        if lang not in seen:
            seen.append(lang)
    return tuple(seen)


# -- shared chrome ------------------------------------------------------------

_STYLE = """
:root {
  --primary: #0E7C66; --primary-d: #0A5A4A; --accent: #E2901F;
  --ink: #16302B; --ink-soft: #5C6E69; --line: #D9E4DF; --danger: #C73E3E;
}
* { box-sizing: border-box; }
body {
  font-family: "Noto Sans", "Noto Sans Devanagari", system-ui, sans-serif;
  color: var(--ink); margin: 0; font-size: 12pt; line-height: 1.5;
}
@page { size: A4; margin: 14mm; }
.sheet { page-break-after: always; padding: 0; }
.sheet:last-child { page-break-after: auto; }
.masthead {
  display: flex; justify-content: space-between; align-items: flex-end;
  border-bottom: 3px solid var(--primary); padding-bottom: 8px; margin-bottom: 14px;
}
.masthead h1 { font-size: 18pt; margin: 0; color: var(--primary-d); }
.masthead .dept { font-size: 13pt; color: var(--ink-soft); }
.banner {
  background: var(--accent); color: #fff; font-weight: 700; letter-spacing: .04em;
  padding: 6px 12px; border-radius: 6px; font-size: 10pt; text-transform: uppercase;
}
.patient-strip {
  display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 10px;
  margin-bottom: 16px; font-size: 11pt;
}
.patient-strip .field { border-bottom: 1.5px solid var(--ink); padding-top: 18px; }
.patient-strip .label { color: var(--ink-soft); font-size: 9pt; }
.q { margin-bottom: 12px; break-inside: avoid; }
.q .qtext { font-weight: 600; margin-bottom: 4px; }
.q .qtext .alt { color: var(--ink-soft); font-weight: 400; }
.opts { display: flex; flex-wrap: wrap; gap: 6px 18px; padding-left: 4px; }
.opt { font-size: 11pt; }
.box { color: var(--primary); font-size: 13pt; margin-right: 4px; }
.blank { border-bottom: 1.5px solid var(--line); min-width: 60px; display: inline-block; }
.scale { display: flex; gap: 10px; }
.scale .dot { width: 26px; height: 26px; border: 2px solid var(--ink-soft);
  border-radius: 50%; text-align: center; line-height: 24px; font-size: 11pt; }
.foot { margin-top: 10px; color: var(--ink-soft); font-size: 9pt;
  border-top: 1px solid var(--line); padding-top: 6px; }
/* token block sheet */
.tokens { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.tok {
  border: 2px dashed var(--line); border-radius: 8px; text-align: center;
  padding: 10px 0; break-inside: avoid;
}
.tok .num { font-size: 26pt; font-weight: 800; color: var(--primary-d); }
.tok .cap { font-size: 8pt; color: var(--ink-soft); }
.hint { color: var(--ink-soft); font-size: 10pt; margin: 4px 0 14px; }
"""


def _doc(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


def _masthead(hospital: str, dept: str, sub: str) -> str:
    return (
        "<div class='masthead'><div>"
        f"<h1>{escape(hospital)}</h1>"
        f"<div class='dept'>{escape(dept)}</div></div>"
        f"<div class='banner'>{escape(sub)}</div></div>"
    )


_PATIENT_STRIP = (
    "<div class='patient-strip'>"
    "<div class='field'><div class='label'>Name / नाम</div></div>"
    "<div class='field'><div class='label'>Age / आयु</div></div>"
    "<div class='field'><div class='label'>Sex / लिंग</div></div>"
    "<div class='field'><div class='label'>Token / टोकन</div></div>"
    "</div>"
)


# -- intake sheets ------------------------------------------------------------


def _question(node: dict[str, Any], langs: tuple[Lang, ...]) -> str:
    primary, *rest = langs
    text_main = _text(node.get("text"), primary)
    alts = " ".join(
        f"<span class='alt'>{escape(_text(node.get('text'), lang))}</span>" for lang in rest
    )
    head = f"<div class='qtext'>{escape(text_main)} {alts}</div>"

    kind = node.get("type")
    body = ""
    if kind in ("single", "multi"):
        opts = []
        for opt in node.get("options", []):
            label = _text(opt.get("text"), primary)
            alt = _text(opt.get("text"), rest[0]) if rest else ""
            alt_html = f" <span class='alt'>{escape(alt)}</span>" if alt and alt != label else ""
            opts.append(
                f"<span class='opt'><span class='box'>{_BOX}</span>"
                f"{escape(label)}{alt_html}</span>"
            )
        body = f"<div class='opts'>{''.join(opts)}</div>"
    elif kind == "scale":
        lo = int(node.get("min") or 1)
        hi = int(node.get("max") or 5)
        dots = "".join(f"<span class='dot'>{n}</span>" for n in range(lo, hi + 1))
        body = f"<div class='scale'>{dots}</div>"
    elif kind == "number":
        unit = node.get("unit") or ""
        body = (
            "<div class='opts'><span class='blank' style='min-width:120px'></span> "
            f"{escape(unit)}</div>"
        )
    elif kind == "body_map":
        body = (
            "<div class='opts'>Front / आगे: "
            "<span class='blank' style='min-width:160px'></span> &nbsp; Back / पीछे: "
            "<span class='blank' style='min-width:160px'></span></div>"
        )
    else:  # free_voice / anything else — a couple of write-in lines
        body = "<div><span class='blank' style='min-width:100%'>&nbsp;</span></div>"
    return f"<div class='q'>{head}{body}</div>"


def render_intake_sheet(
    tree_json: dict[str, Any],
    *,
    hospital_name: str,
    department_name: str,
    langs: list[Lang] | tuple[Lang, ...] | None = None,
) -> str:
    """One department's fillable intake form, rendered from its tree."""
    langs = _langs(langs)
    title = _text(tree_json.get("title"), langs[0]) or tree_json.get("key", "Intake")
    questions = "".join(_question(node, langs) for node in tree_json.get("nodes", []))
    ref = f"{tree_json.get('key')}@v{tree_json.get('version')}"
    body = (
        "<div class='sheet'>"
        + _masthead(hospital_name, department_name, "Downtime paper intake")
        + f"<div class='hint'>{escape(title)}</div>"
        + _PATIENT_STRIP
        + questions
        + f"<div class='foot'>Tree {escape(ref)} · "
        "Enter into the system on recovery via Coordinator → Downtime → Paper entry.</div>"
        "</div>"
    )
    return body


def render_intake_sheets(
    sheets: list[tuple[dict[str, Any], str]],
    *,
    hospital_name: str,
    langs: list[Lang] | tuple[Lang, ...] | None = None,
) -> str:
    """A full print job: `[(tree_json, department_name)]`, one page each."""
    inner = "".join(
        render_intake_sheet(
            tree_json, hospital_name=hospital_name, department_name=dept_name, langs=langs
        )
        for tree_json, dept_name in sheets
    )
    return _doc("Downtime intake sheets", inner)


# -- token block sheet --------------------------------------------------------


def render_token_block_sheet(
    blocks: list[dict[str, Any]],
    *,
    hospital_name: str,
    kiosk_id: str,
    date_str: str,
) -> str:
    """Tear-off token numerals for a kiosk's offline blocks (doc 01 §5 pt 3).

    `blocks` is `[{department_name, start_no, end_no}]`. Each block prints its
    full range as a grid of big dashed-box numerals staff tear off and hand out
    in order during a blackout — the numbers the online allocator can never reach.
    """
    pages = []
    for block in blocks:
        start = int(block["start_no"])
        end = int(block["end_no"])
        dept = str(block["department_name"])
        toks = "".join(
            f"<div class='tok'><div class='num'>{n}</div>"
            f"<div class='cap'>{escape(dept)}</div></div>"
            for n in range(start, end + 1)
        )
        pages.append(
            "<div class='sheet'>"
            + _masthead(hospital_name, dept, "Downtime token block")
            + f"<div class='hint'>Kiosk <b>{escape(kiosk_id)}</b> · {escape(date_str)} · "
            f"tokens {start}–{end}. Hand out in order; note the token on each paper sheet.</div>"
            + f"<div class='tokens'>{toks}</div>"
            "</div>"
        )
    return _doc("Downtime token block", "".join(pages))
