"""The switchboard: which channels are open, and what a closed one says (S-GL.1, doc 12 §1).

Before this module the system had no honest "off". A patient who messaged the
hospital's WhatsApp number reached a bot that tried, failed per message and said
nothing useful; a channel was "not live" only in the sense that nobody had told
anyone the number. Doc 12 §1 names that the single most valuable missing switch,
because it is also how the pilot goes live before Meta and Exotel are provisioned.

Two facts decide whether a channel is open, and they are kept apart on purpose:

- **The switch** — `enabled` in the published channel document (`app.tiers`),
  which is the operator's decision and nothing else.
- **Readiness** — whether the vendor a channel needs is actually configured,
  which is computed from settings and cannot be lied about from a console.

A channel is open only if both hold. Keeping them separate is what lets the
console say *why* a channel is dark: "switched off" and "no Meta credentials" are
different problems with different fixes, and a single boolean would collapse them
into the failure doc 12 §4 describes — a half-configured channel that fails badly
rather than staying shut.

`require_open` is the gate every entry point calls. It raises `ChannelClosed`,
which `app.main` renders as a civil 503 naming the desk, never a 500 and never a
half-flow.
"""

from __future__ import annotations

from app.channels.state import (
    ChannelClosed,
    ChannelState,
    channel_state,
    channel_states,
    readiness,
    require_open,
)
from app.channels.store import published_config, resolve_config

__all__ = [
    "ChannelClosed",
    "ChannelState",
    "channel_state",
    "channel_states",
    "published_config",
    "readiness",
    "require_open",
    "resolve_config",
]
