"""WhatsApp bot (S12, doc 03 §1d) — the intake engine's second channel.

The kiosk (S6) was the engine's first HTTP surface; this is the second, and it
reuses the same `IntakeEngine` and the same four-tool contract. Where the kiosk
holds its `session_id` in the browser, WhatsApp is stateful on the *server* side
of a webhook: Meta delivers one inbound message at a time keyed by the patient's
phone (`wa_id`), so the mapping wa_id → live intake session lives here, in the
`Conversation` state, alongside the one thing WhatsApp forces on us that no other
channel has — the **24-hour session window** (doc 03 §1d).

Layout:
  templates      the pre-approved template registry (out-of-window sends)
  conversation   Conversation state + store (wa_id → session, 24h window)
  render         one intake Node → WhatsApp interactive (buttons / list)
  bot            the service: inbound message → intake turn / command intent
"""

from app.whatsapp.conversation import (
    Conversation,
    ConversationStep,
    ConversationStore,
    InMemoryConversationStore,
    RedisConversationStore,
    build_conversation_store,
)
from app.whatsapp.templates import (
    Template,
    TemplateError,
    get_template,
    template_message,
)

__all__ = [
    "Conversation",
    "ConversationStep",
    "ConversationStore",
    "InMemoryConversationStore",
    "RedisConversationStore",
    "Template",
    "TemplateError",
    "build_conversation_store",
    "get_template",
    "template_message",
]
