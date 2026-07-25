"""Pre-approved WhatsApp template registry (S12, doc 03 §1d).

> "Session windows handled correctly (template messages outside 24h window;
>  templates pre-approved list in repo)." — doc 03 §1d

Meta only accepts a *registered* template message when the 24-hour customer
service window is closed. A template is approved by Meta ahead of time, per name
**and per language**, with a fixed body and numbered placeholders (`{{1}}`); the
only thing a send may vary is the placeholder values. Free text out of window is
rejected at the API.

This file is that "pre-approved list in repo": the single source of truth both
the bot (a proactive nudge, a token-status reply after the window lapsed) and the
S11 prescription delivery consult before an out-of-window send. Registering the
template *here* is not the same as it being approved *at Meta* — that is an
account action a human does in the WhatsApp Manager, and STATE.md records that no
template has ever actually been approved. What this file guarantees is that our
code never tries to send a template shape Meta has not seen from us, and never
sends the wrong number of variables (a silent Meta rejection otherwise).

Bodies are carried in every active pilot language (en + hi + mr + te, doc 03 §1;
mr/te completed in S13). A missing language is a registry error, not a silent
fall-back to English — an out-of-window message the patient cannot read is worse
than none. The `app.lang_qa` harness asserts every template covers all four.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.enums import Lang
from app.providers.messaging import OutboundMessage

#: Meta's placeholder token: {{1}}, {{2}}, ... — positional, 1-indexed.
_PLACEHOLDER = re.compile(r"\{\{(\d+)\}\}")


class TemplateError(Exception):
    """A template lookup or a variable-count mismatch — caught before the wire."""


@dataclass(frozen=True, slots=True)
class Template:
    """One approved template, in one language.

    `variables` names each placeholder for the humans reading this file; the
    machine only cares that its length matches the `{{n}}` tokens in `body`, which
    `__post_init__` checks so a drifted body/variable pair fails at import, not in
    the field.
    """

    #: Meta's template name (lowercase, underscores — Meta's own constraint).
    name: str
    lang: Lang
    #: Meta category. UTILITY = a transactional follow-up to something the patient
    #: did (a visit, an intake); it is the category every template here uses.
    category: str
    body: str
    #: Human-readable name per placeholder, in order. Length == placeholder count.
    variables: Sequence[str] = ()

    def __post_init__(self) -> None:
        indices = [int(m) for m in _PLACEHOLDER.findall(self.body)]
        expected = list(range(1, len(indices) + 1))
        if sorted(indices) != expected:
            raise TemplateError(
                f"template {self.name!r}/{self.lang}: placeholders {indices} are not a "
                f"1..N run; Meta numbers them positionally"
            )
        if len(self.variables) != len(indices):
            raise TemplateError(
                f"template {self.name!r}/{self.lang}: {len(indices)} placeholders but "
                f"{len(self.variables)} variables named"
            )

    @property
    def placeholder_count(self) -> int:
        return len(self.variables)

    def preview(self, values: Sequence[str]) -> str:
        """Fill the body for a log line or a test — never sent to Meta (Meta fills
        it from the parameters), but the exact text the patient will see."""
        self._check(values)
        return _PLACEHOLDER.sub(lambda m: values[int(m.group(1)) - 1], self.body)

    def _check(self, values: Sequence[str]) -> None:
        if len(values) != self.placeholder_count:
            raise TemplateError(
                f"template {self.name!r}/{self.lang} wants {self.placeholder_count} "
                f"variables, got {len(values)}"
            )


# -- the registry -------------------------------------------------------------
#
# Keyed (name, lang). Adding a template here is only half the job — the same body
# must be submitted for approval in the WhatsApp Manager before it will send.


_REGISTRY: dict[tuple[str, Lang], Template] = {}


def _register(*templates: Template) -> None:
    for t in templates:
        _REGISTRY[(t.name, t.lang)] = t


_register(
    # A proactive nudge to begin intake before a visit — the "reminder messages"
    # entry point (doc 03 §1d). Opens a fresh 24h window when the patient replies.
    Template(
        name="intake_invite",
        lang=Lang.EN,
        category="UTILITY",
        body=(
            "Namaste {{1}}. This is {{2}}. Reply to this message to complete your "
            "check-in before your visit, so the doctor is ready for you."
        ),
        variables=("patient_name", "hospital"),
    ),
    Template(
        name="intake_invite",
        lang=Lang.HI,
        category="UTILITY",
        body=(
            "नमस्ते {{1}}। यह {{2}} है। अपनी विजिट से पहले चेक-इन पूरा करने के लिए इस "
            "संदेश का उत्तर दें, ताकि डॉक्टर आपके लिए तैयार रहें।"
        ),
        variables=("patient_name", "hospital"),
    ),
    Template(
        name="intake_invite",
        lang=Lang.MR,
        category="UTILITY",
        body=(
            "नमस्कार {{1}}. हे {{2}} आहे. तुमच्या भेटीपूर्वी चेक-इन पूर्ण करण्यासाठी या "
            "संदेशाला उत्तर द्या, म्हणजे डॉक्टर तुमच्यासाठी तयार राहतील."
        ),
        variables=("patient_name", "hospital"),
    ),
    Template(
        name="intake_invite",
        lang=Lang.TE,
        category="UTILITY",
        body=(
            "నమస్తే {{1}}. ఇది {{2}}. మీ సందర్శనకు ముందు చెక్-ఇన్ పూర్తి చేయడానికి ఈ "
            "సందేశానికి ప్రత్యుత్తరం ఇవ్వండి, తద్వారా డాక్టర్ మీ కోసం సిద్ధంగా ఉంటారు."
        ),
        variables=("patient_name", "hospital"),
    ),
    # A token-status answer once the window has lapsed (the in-window answer is
    # free text; this is the out-of-window fall-back, doc 03 §6/§1d).
    Template(
        name="token_status",
        lang=Lang.EN,
        category="UTILITY",
        body="Namaste {{1}}. Your token today at {{2}} is {{3}}. {{4}} ahead of you.",
        variables=("patient_name", "hospital", "token_no", "ahead"),
    ),
    Template(
        name="token_status",
        lang=Lang.HI,
        category="UTILITY",
        body="नमस्ते {{1}}। आज {{2}} में आपका टोकन {{3}} है। आपसे पहले {{4}}।",
        variables=("patient_name", "hospital", "token_no", "ahead"),
    ),
    Template(
        name="token_status",
        lang=Lang.MR,
        category="UTILITY",
        body="नमस्कार {{1}}. आज {{2}} मध्ये तुमचा टोकन {{3}} आहे. तुमच्या आधी {{4}}.",
        variables=("patient_name", "hospital", "token_no", "ahead"),
    ),
    Template(
        name="token_status",
        lang=Lang.TE,
        category="UTILITY",
        body="నమస్తే {{1}}. ఈ రోజు {{2}}లో మీ టోకెన్ {{3}}. మీకు ముందు {{4}}.",
        variables=("patient_name", "hospital", "token_no", "ahead"),
    ),
    # An out-of-window prescription notification (S11 delivery). The full sheet is
    # sent as free text once the patient replies (which opens a window); Meta will
    # not accept the whole prescription as free text while the window is closed, so
    # this invites the reply. The bot answers that reply with the sheet.
    Template(
        name="prescription_ready",
        lang=Lang.EN,
        category="UTILITY",
        body=(
            "Namaste {{1}}. Dr. {{2}} has issued your prescription at {{3}}. Reply to "
            "this message and we will send it to you here."
        ),
        variables=("patient_name", "doctor", "hospital"),
    ),
    Template(
        name="prescription_ready",
        lang=Lang.HI,
        category="UTILITY",
        body=(
            "नमस्ते {{1}}। डॉ. {{2}} ने {{3}} में आपका प्रिस्क्रिप्शन जारी किया है। इस संदेश "
            "का उत्तर दें और हम इसे यहाँ भेज देंगे।"
        ),
        variables=("patient_name", "doctor", "hospital"),
    ),
    Template(
        name="prescription_ready",
        lang=Lang.MR,
        category="UTILITY",
        body=(
            "नमस्कार {{1}}. डॉ. {{2}} यांनी {{3}} मध्ये तुमचं प्रिस्क्रिप्शन दिलं आहे. या "
            "संदेशाला उत्तर द्या आणि आम्ही ते तुम्हाला इथे पाठवू."
        ),
        variables=("patient_name", "doctor", "hospital"),
    ),
    Template(
        name="prescription_ready",
        lang=Lang.TE,
        category="UTILITY",
        body=(
            "నమస్తే {{1}}. డా. {{2}} {{3}}లో మీ ప్రిస్క్రిప్షన్ ఇచ్చారు. ఈ సందేశానికి "
            "ప్రత్యుత్తరం ఇవ్వండి, మేము దాన్ని ఇక్కడ మీకు పంపుతాము."
        ),
        variables=("patient_name", "doctor", "hospital"),
    ),
)


def get_template(name: str, lang: Lang) -> Template:
    """Look up a registered template, or raise. A `TemplateError` here means the
    code asked for a template/language pair that is not in the repo list — a bug to
    fix before it reaches a patient, never something to paper over with English."""
    try:
        return _REGISTRY[(name, lang)]
    except KeyError:
        raise TemplateError(f"no registered template {name!r} for language {lang}") from None


def template_message(
    *, to: str, name: str, lang: Lang, variables: Sequence[str]
) -> OutboundMessage:
    """Build the `OutboundMessage` for an out-of-window template send.

    Validates the variable count against the registered template *before* the
    provider is touched, so a mismatch surfaces here (with the template name) and
    never as an opaque Meta 400.
    """
    template = get_template(name, lang)
    template._check(variables)
    return OutboundMessage(
        to=to,
        template_name=template.name,
        template_lang=str(template.lang),
        template_variables=tuple(variables),
    )
