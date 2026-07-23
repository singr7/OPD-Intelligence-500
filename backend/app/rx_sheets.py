"""The prescription on paper (doc 03 §8) — two copies of one record.

`app.prescription` decides *what* is prescribed; this decides how it is read.
Two audiences, two sheets, same `Prescription.meds` snapshot:

* **Clinical copy** — hospital letterhead, for the file and the pharmacy.
  Dense: drug, dose, route, frequency as dictated, duration. Carries the
  formulary flags, because the pharmacist is the last person who can catch a
  name the doctor acknowledged but never said.
* **Patient copy** — large type, one drug per band, morning/afternoon/night
  **pictograms**, in the patient's own language. Doc 04's audio-first laws have a
  paper cousin: this sheet is for someone who may not read at all, so the icons
  carry the instruction and the words support them, never the reverse.

## Why HTML rather than a server-rendered PDF

Same decision as `app.print_sheets` (S8), for the same reason: these sheets must
render Devanagari, and embedding Indic fonts with correct shaping inside a
hand-rolled PDF is fragile, while a real HTML→PDF engine (WeasyPrint/pango) needs
native libraries the image does not ship. So this returns print-optimised HTML
that the browser turns into a PDF. A server-side PDF is a deploy-dependency
decision, and it is the same one for both sheet families (backlog, S19/S21).

## The pictogram rule

A pictogram is only drawn when the dictation **stated** a time of day
(`Schedule.slots_known`). A drug whose frequency was a bare count gets that many
tablet glyphs and no sun/moon; a drug whose frequency could not be read at all
gets the doctor's words and nothing else. See `app.prescription.parse_schedule` —
the icons are an instruction to someone who cannot read the line beside them, so
inventing one is inventing a dose.
"""

from __future__ import annotations

from datetime import date
from html import escape

from app.models.enums import Lang
from app.prescription import RxLine, Schedule

#: Patient-facing strings. English + Hindi ship now; mr/te land with S13, and
#: fall back to English rather than showing a Devanagari string to a Telugu
#: speaker.
_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Your medicines",
        "morning": "Morning",
        "afternoon": "Afternoon",
        "night": "Night",
        "per_day": "times a day",
        "as_told": "as the doctor said",
        "duration": "for",
        "advice": "Also remember",
        "follow_up": "Come back on",
        "ask": "Ask the pharmacist if anything here is unclear.",
        "flagged": "Confirm this one with the doctor",
        "patient_copy": "Patient copy",
    },
    "hi": {
        "title": "आपकी दवाइयाँ",
        "morning": "सुबह",
        "afternoon": "दोपहर",
        "night": "रात",
        "per_day": "बार रोज़",
        "as_told": "जैसा डॉक्टर ने कहा",
        "duration": "कितने दिन",
        "advice": "यह भी ध्यान रखें",
        "follow_up": "अगली बार आइए",
        "ask": "कुछ समझ न आए तो फार्मासिस्ट से पूछें।",
        "flagged": "यह दवा डॉक्टर से एक बार पूछ लें",
        "patient_copy": "मरीज़ की प्रति",
    },
}

#: Duotone-ish glyphs rather than an icon font: a print stylesheet cannot rely on
#: a webfont being embedded by whatever browser prints it, and these three shapes
#: are unambiguous at 32pt on a laser printer.
_SUN = "☀"
_MIDDAY = "☼"
_MOON = "☾"
_TABLET = "⬤"


def _s(lang: Lang | str, key: str) -> str:
    table = _STRINGS.get(str(lang)) or _STRINGS["en"]
    return table.get(key) or _STRINGS["en"][key]


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
.sheet { page-break-after: always; }
.sheet:last-child { page-break-after: auto; }
.masthead {
  display: flex; justify-content: space-between; align-items: flex-end;
  border-bottom: 3px solid var(--primary); padding-bottom: 8px; margin-bottom: 12px;
}
.masthead h1 { font-size: 18pt; margin: 0; color: var(--primary-d); }
.masthead .dept { font-size: 12pt; color: var(--ink-soft); }
.banner {
  background: var(--primary); color: #fff; font-weight: 700; letter-spacing: .04em;
  padding: 6px 12px; border-radius: 6px; font-size: 10pt; text-transform: uppercase;
}
.who { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 10px;
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; margin-bottom: 14px; }
.who .label { color: var(--ink-soft); font-size: 8.5pt; text-transform: uppercase;
  letter-spacing: .04em; }
.who .value { font-size: 12pt; font-weight: 600; }
.dx { margin-bottom: 12px; }
.dx .label { color: var(--ink-soft); font-size: 9pt; text-transform: uppercase; }
.dx .value { font-size: 13pt; font-weight: 600; }
/* clinical table */
table.rx { width: 100%; border-collapse: collapse; margin-bottom: 14px; }
table.rx th {
  text-align: left; font-size: 9pt; text-transform: uppercase; letter-spacing: .04em;
  color: var(--ink-soft); border-bottom: 2px solid var(--line); padding: 6px 8px;
}
table.rx td { padding: 8px; border-bottom: 1px solid var(--line); vertical-align: top;
  break-inside: avoid; }
table.rx .name { font-weight: 700; font-size: 12.5pt; }
table.rx .spoken { color: var(--ink-soft); font-size: 9pt; font-style: italic; }
tr.flagged td { background: #FDF3F3; }
tr.flagged .name { color: var(--danger); }
.flag {
  display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px;
  background: var(--danger); color: #fff; font-size: 8pt; font-weight: 700;
  text-transform: uppercase; letter-spacing: .03em; vertical-align: middle;
}
.flag-why { color: var(--danger); font-size: 9pt; }
.block { margin-bottom: 12px; }
.block h3 { font-size: 10pt; text-transform: uppercase; letter-spacing: .04em;
  color: var(--ink-soft); margin: 0 0 4px; }
.block ul { margin: 0; padding-left: 18px; }
.sign { margin-top: 26px; display: flex; justify-content: flex-end; }
.sign .line { width: 240px; border-top: 1.5px solid var(--ink); padding-top: 6px;
  text-align: center; font-size: 10pt; }
.sign .name { font-weight: 700; font-size: 11pt; }
.foot { margin-top: 12px; color: var(--ink-soft); font-size: 8.5pt;
  border-top: 1px solid var(--line); padding-top: 6px; }
/* patient copy — large type, icon-led */
.p-title { font-size: 22pt; font-weight: 800; color: var(--primary-d); margin: 0 0 10px; }
.med { border: 2px solid var(--line); border-radius: 12px; padding: 12px 14px;
  margin-bottom: 12px; break-inside: avoid; display: grid;
  grid-template-columns: 1fr auto; gap: 12px; align-items: center; }
.med.flagged { border-color: var(--danger); }
.med .mname { font-size: 19pt; font-weight: 800; line-height: 1.2; }
.med .mdose { font-size: 13pt; color: var(--ink-soft); }
.med .mdur { font-size: 13pt; margin-top: 4px; }
.slots { display: flex; gap: 14px; }
.slot { text-align: center; min-width: 62px; }
.slot .icon { font-size: 30pt; line-height: 1; color: var(--line); }
.slot.on .icon { color: var(--accent); }
.slot.on.night .icon { color: var(--primary-d); }
.slot .cap { font-size: 10.5pt; color: var(--ink-soft); }
.slot.on .cap { color: var(--ink); font-weight: 700; }
.count { text-align: center; }
.count .pills { font-size: 20pt; letter-spacing: 4px; color: var(--accent); }
.count .cap { font-size: 11pt; }
.words { font-size: 13pt; border: 2px dashed var(--line); border-radius: 8px;
  padding: 6px 10px; color: var(--ink); max-width: 240px; }
.p-note { font-size: 12pt; margin-top: 4px; }
.p-flag { color: var(--danger); font-weight: 700; font-size: 11pt; margin-top: 4px; }
.p-foot { margin-top: 16px; font-size: 12pt; color: var(--ink-soft);
  border-top: 1px solid var(--line); padding-top: 8px; }
"""


def _doc(title: str, body: str, lang: Lang | str = Lang.EN) -> str:
    return (
        f"<!doctype html><html lang='{escape(str(lang))}'><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


def _field(label: str, value: str) -> str:
    return (
        f"<div><div class='label'>{escape(label)}</div>"
        f"<div class='value'>{escape(value)}</div></div>"
    )


# -- clinical copy ------------------------------------------------------------


def render_clinical_copy(
    *,
    lines: tuple[RxLine, ...],
    hospital: str,
    department: str,
    doctor_name: str,
    doctor_reg_no: str,
    doctor_qualification: str | None,
    patient_name: str,
    patient_mrn: str,
    patient_age: int | None,
    patient_sex: str | None,
    visit_date: date,
    token_no: int | None,
    diagnosis: str | None = None,
    advice: tuple[str, ...] = (),
    follow_up: str | None = None,
) -> str:
    """The letterhead copy — for the file, the desk and the pharmacy."""
    rows = "".join(_clinical_row(line) for line in lines)
    body = [
        "<div class='sheet'>",
        _masthead(hospital, department, "Prescription"),
        "<div class='who'>",
        _field("Patient", patient_name),
        _field("MRN", patient_mrn),
        _field("Age / Sex", _age_sex(patient_age, patient_sex)),
        _field("Date / Token", _date_token(visit_date, token_no)),
        "</div>",
    ]
    if diagnosis:
        body.append(
            f"<div class='dx'><div class='label'>Diagnosis</div>"
            f"<div class='value'>{escape(diagnosis)}</div></div>"
        )
    body.append(
        "<table class='rx'><thead><tr>"
        "<th>Drug</th><th>Dose</th><th>Route</th><th>Frequency</th><th>Duration</th>"
        "</tr></thead><tbody>"
        + (rows or "<tr><td colspan='5'>No medicines prescribed.</td></tr>")
        + "</tbody></table>"
    )
    if advice:
        items = "".join(f"<li>{escape(item)}</li>" for item in advice)
        body.append(f"<div class='block'><h3>Advice</h3><ul>{items}</ul></div>")
    if follow_up:
        body.append(f"<div class='block'><h3>Follow-up</h3><div>{escape(follow_up)}</div></div>")
    qualification = f" · {doctor_qualification}" if doctor_qualification else ""
    body.append(
        "<div class='sign'><div class='line'>"
        f"<div class='name'>{escape(doctor_name)}</div>"
        f"<div>Reg. {escape(doctor_reg_no)}{escape(qualification)}</div>"
        "</div></div>"
    )
    body.append(
        "<div class='foot'>Digitally signed in the doctor's console. "
        "Drug names are printed exactly as dictated; flagged rows were "
        "acknowledged by the doctor but are not confirmed against the hospital "
        "formulary.</div></div>"
    )
    return _doc(f"Prescription — {patient_name}", "".join(body))


def _clinical_row(line: RxLine) -> str:
    med = line.med
    flag = (
        f"<span class='flag'>check</span>"
        f"<div class='flag-why'>{escape(line.flag_reason or '')}</div>"
        if line.flagged
        else ""
    )
    spoken = f"<div class='spoken'>“{escape(med.as_spoken)}”</div>" if med.as_spoken else ""
    return (
        f"<tr class='{'flagged' if line.flagged else ''}'>"
        f"<td><span class='name'>{escape(med.name)}</span>{flag}{spoken}</td>"
        f"<td>{escape(med.dose or '—')}</td>"
        f"<td>{escape(med.route or '—')}</td>"
        f"<td>{escape(med.freq or '—')}</td>"
        f"<td>{escape(med.duration or '—')}</td>"
        "</tr>"
    )


# -- patient copy -------------------------------------------------------------


def render_patient_copy(
    *,
    lines: tuple[RxLine, ...],
    lang: Lang | str,
    hospital: str,
    department: str,
    patient_name: str,
    visit_date: date,
    token_no: int | None,
    advice: tuple[str, ...] = (),
    follow_up: str | None = None,
) -> str:
    """The large-type, icon-led copy the patient carries home.

    Every drug is one band: name big enough to match against the strip in their
    hand, then the schedule as icons when — and only when — the dictation said
    what time of day it was.
    """
    bands = "".join(_patient_band(line, lang) for line in lines)
    body = [
        "<div class='sheet'>",
        _masthead(hospital, department, _s(lang, "patient_copy")),
        f"<div class='p-title'>{escape(_s(lang, 'title'))}</div>",
        "<div class='who'>",
        _field("", patient_name),
        _field("", _date_token(visit_date, token_no)),
        "</div>",
        bands or "",
    ]
    if advice:
        items = "".join(f"<li>{escape(item)}</li>" for item in advice)
        body.append(
            f"<div class='block'><h3>{escape(_s(lang, 'advice'))}</h3>"
            f"<ul class='p-note'>{items}</ul></div>"
        )
    if follow_up:
        body.append(
            f"<div class='block'><h3>{escape(_s(lang, 'follow_up'))}</h3>"
            f"<div class='p-note'>{escape(follow_up)}</div></div>"
        )
    body.append(f"<div class='p-foot'>{escape(_s(lang, 'ask'))}</div></div>")
    return _doc(f"{_s(lang, 'title')} — {patient_name}", "".join(body), lang)


def _patient_band(line: RxLine, lang: Lang | str) -> str:
    med = line.med
    duration = (
        f"<div class='mdur'>{escape(_s(lang, 'duration'))} {escape(med.duration)}</div>"
        if med.duration
        else ""
    )
    flag = f"<div class='p-flag'>{escape(_s(lang, 'flagged'))}</div>" if line.flagged else ""
    return (
        f"<div class='med {'flagged' if line.flagged else ''}'>"
        "<div>"
        f"<div class='mname'>{escape(med.name)}</div>"
        f"<div class='mdose'>{escape(med.dose or '')}</div>"
        f"{duration}{flag}"
        "</div>"
        f"{_schedule_art(line.schedule, lang, med.freq)}"
        "</div>"
    )


def _schedule_art(schedule: Schedule | None, lang: Lang | str, freq: str | None) -> str:
    """Icons, tablet count, or the doctor's words — in that order of certainty.

    The three branches are the three states `parse_schedule` can return, and the
    ordering is deliberate: only the first one draws a time of day, and it draws
    it only because the dictation named it. The last one is not a failure — "SOS"
    and "alternate days" are ordinary prescriptions that three icons cannot say,
    so the patient gets the doctor's phrase and a pharmacist to ask.
    """
    if schedule is None:
        words = (freq or "").strip()
        if not words:
            return ""
        return f"<div class='words'>{escape(words)}</div>"

    if schedule.slots_known:
        return (
            "<div class='slots'>"
            + _slot(_SUN, _s(lang, "morning"), schedule.morning, "")
            + _slot(_MIDDAY, _s(lang, "afternoon"), schedule.afternoon, "")
            + _slot(_MOON, _s(lang, "night"), schedule.night, "night")
            + "</div>"
        )

    # Count without a time of day: say how many, refuse to say when.
    count = schedule.per_day or 0
    return (
        "<div class='count'>"
        f"<div class='pills'>{_TABLET * count}</div>"
        f"<div class='cap'>{count} {escape(_s(lang, 'per_day'))}</div>"
        "</div>"
    )


def _slot(icon: str, caption: str, on: bool, extra: str) -> str:
    classes = " ".join(part for part in ("slot", "on" if on else "", extra) if part)
    return (
        f"<div class='{classes}'>"
        f"<div class='icon'>{icon}</div>"
        f"<div class='cap'>{escape(caption)}</div>"
        "</div>"
    )


# -- shared -------------------------------------------------------------------


def _masthead(hospital: str, dept: str, sub: str) -> str:
    return (
        "<div class='masthead'><div>"
        f"<h1>{escape(hospital)}</h1>"
        f"<div class='dept'>{escape(dept)}</div></div>"
        f"<div class='banner'>{escape(sub)}</div></div>"
    )


def _age_sex(age: int | None, sex: str | None) -> str:
    parts = [str(age) if age is not None else "—", (sex or "—").upper()[:1]]
    return " / ".join(parts)


def _date_token(visit_date: date, token_no: int | None) -> str:
    stamp = visit_date.strftime("%d %b %Y")
    return f"{stamp} · #{token_no}" if token_no is not None else stamp


def sms_body(*, lines: tuple[RxLine, ...], hospital: str, lang: Lang | str) -> str:
    """The SMS fallback when a patient has no WhatsApp.

    Deliberately drug names + schedule words only: an SMS has no pictograms and
    no formatting, so it is a *reminder* of the paper they are holding, never a
    replacement for it.
    """
    names = ", ".join(line.med.name for line in lines[:5])
    more = f" +{len(lines) - 5}" if len(lines) > 5 else ""
    return f"{hospital}: {_s(lang, 'title')} — {names}{more}. {_s(lang, 'ask')}"
