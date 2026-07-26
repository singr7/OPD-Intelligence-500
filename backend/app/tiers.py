"""The channel document: per-channel tier ladder, enablement, capacity (doc 08 §3/§5, doc 12 §1).

Loads `config/tiers.yaml` — the data that says, per channel, whether it is open at
all (`enabled`), which tiers to try in what order (`ladder`), how many concurrent
local voice sessions that channel may hold (`max_concurrent`), how many the one
GPU may hold in total (`admission.max_oss_sessions`), and how the D-1 outbound
campaign splits tomorrow's list between calling and messaging (`campaign.mix`).
Validated on load so a typo'd channel or an unknown tier label fails loudly at
boot, where it is cheap, rather than silently routing a channel to nowhere at 9am.

**Why it is one document and not a row per channel** (S-GL.1): the cross-checks
are document-wide — a channel's `max_concurrent` cannot exceed the global
`max_oss_sessions`, and the campaign mix must sum to 100 across channels that are
themselves open. A per-channel editor would pass row-level validation and fail
those checks at 6pm when beat launches the campaign. Same reasoning, and the same
shape, as `ProtocolBankVersion` (see `app/models/content.py`).

`parse_tier_config` is the **only** constructor, so a document that arrives from
the admin console's editor is checked exactly as the file is; `app.channels.store`
overlays a published row on top of the file, and the file stays the floor.

**The ladder is labels, not a new tier enum** (see the yaml header and doc 08 §5):
`v_oss` is the existing V2 pipeline / V1 realtime backed by local providers, so
adding it needs no `IntakeTier` value and no engine surgery. This module hands the
ladder to whatever consumes it:

- `AdmissionController` (built here) gates the local realtime session count — the
  one piece already load-bearing in the software half.
- `ladder_for(channel)` is the ordered preference a channel's voice entrypoint
  reads to decide "try local, then cloud, then zero-AI". Wiring it into the
  voice-gw / engine realtime path — routing an over-cap or unhealthy channel down
  its ladder — is S-OSS.2, when there is a live local session to route.

Kept deliberately small: config + validation + the admission gate. It does not
reach into the registry or the engine; those consume `ladder_for` when the GPU
half lands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.models.enums import Channel
from app.providers.local_oss.admission import AdmissionController

logger = logging.getLogger(__name__)

#: Repo root / config / tiers.yaml (this file is backend/app/tiers.py).
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "tiers.yaml"

#: The tier labels a ladder may name. `v_oss` is doc 08's local pipeline; the
#: rest are the existing V1/V2/V3. A label outside this set is a config bug.
KNOWN_TIERS: frozenset[str] = frozenset({"v1", "v_oss", "v2", "v3"})

#: The admission profile the local voice pipeline reserves seats under. One
#: profile today (all local voice shares the GPU); named so S-OSS.2 can add more
#: if STT and TTS ever get independent caps.
OSS_PROFILE = "v_oss"


#: Channels a patient can start an intake on. `paper` and `sms` are in the
#: `Channel` enum for provenance (what carried a message) but have no entry
#: point to open or close, so the switchboard does not offer them.
SWITCHABLE: tuple[Channel, ...] = (Channel.KIOSK, Channel.PHONE, Channel.WHATSAPP, Channel.APP)


class TierConfigError(ValueError):
    """The channel document is malformed. Raised at parse, never swallowed."""


@dataclass(frozen=True, slots=True)
class ChannelPolicy:
    """One channel's row in the document.

    `enabled` is the operator's switch and nothing else — whether the channel's
    vendor is actually provisioned is a separate, computed fact
    (`app.channels.readiness`), because a switch that silently means "on, unless
    you forgot the credentials" is the failure doc 12 §4 describes.
    """

    ladder: tuple[str, ...]
    enabled: bool = True
    #: Concurrent local (`v_oss`) voice sessions this channel may hold. 0 means
    #: "no channel-specific share" — it competes for the global cap only.
    max_concurrent: int = 0


class TierConfig:
    """The validated channel document — the file's contents, or a published row."""

    def __init__(
        self,
        policies: dict[Channel, ChannelPolicy],
        max_oss_sessions: int,
        campaign_mix: dict[Channel, int] | None = None,
    ) -> None:
        self._policies = policies
        self.max_oss_sessions = max_oss_sessions
        #: {channel: percent} for the D-1 outbound campaign, summing to 100. Empty
        #: means "no mix configured" and the campaign keeps its pre-S-GL.1
        #: behaviour (call everybody it has a number for).
        self.campaign_mix = campaign_mix or {}

    def policy_for(self, channel: Channel) -> ChannelPolicy:
        """A channel's policy, with the safe default for one the document omits:
        open, on the cloud→zero-AI ladder, with no private seat share. A new
        channel that forgot an entry should keep working on the safe path, not
        fail to start and not silently close."""
        return self._policies.get(channel, ChannelPolicy(ladder=("v2", "v3")))

    def ladder_for(self, channel: Channel) -> tuple[str, ...]:
        """The ordered tier preference for a channel, best first."""
        return self.policy_for(channel).ladder

    def is_enabled(self, channel: Channel) -> bool:
        """The switch alone. Ask `app.channels.state` for what a patient gets."""
        return self.policy_for(channel).enabled

    def max_concurrent(self, channel: Channel) -> int:
        return self.policy_for(channel).max_concurrent

    @property
    def channels(self) -> dict[Channel, tuple[str, ...]]:
        return {c: p.ladder for c, p in self._policies.items()}

    @property
    def policies(self) -> dict[Channel, ChannelPolicy]:
        return dict(self._policies)

    def admission_controller(self) -> AdmissionController:
        """An `AdmissionController` capped from `admission.max_oss_sessions`, with
        each channel's `max_concurrent` as its private share of that total."""
        return AdmissionController(
            {OSS_PROFILE: self.max_oss_sessions},
            shares={
                channel.value: policy.max_concurrent
                for channel, policy in self._policies.items()
                if policy.max_concurrent
            },
        )

    def to_json(self) -> dict[str, Any]:
        """The document, in the shape `parse_tier_config` reads back.

        Round-trips: the admin console loads this, edits it, and posts it to a
        draft, where `parse_tier_config` checks it exactly as it checked the file.
        """
        doc: dict[str, Any] = {
            "channels": {
                channel.value: {
                    "ladder": list(policy.ladder),
                    "enabled": policy.enabled,
                    "max_concurrent": policy.max_concurrent,
                }
                for channel, policy in self._policies.items()
            },
            "admission": {"max_oss_sessions": self.max_oss_sessions},
        }
        if self.campaign_mix:
            doc["campaign"] = {"mix": {c.value: pct for c, pct in self.campaign_mix.items()}}
        return doc


def parse_tier_config(data: dict[str, Any]) -> TierConfig:
    """Validate a raw document into a `TierConfig`. Pure — no file I/O, no DB.

    The only constructor. Everything it refuses is something that would otherwise
    be discovered by a patient: an unknown channel, a ladder naming a tier that
    cannot run, a channel reserving more GPU seats than the box has, or a campaign
    mix that does not add up and would therefore drop part of tomorrow's list.
    """
    if not isinstance(data, dict):
        raise TierConfigError("channel document must be a mapping")

    raw_channels = data.get("channels") or {}
    if not isinstance(raw_channels, dict):
        raise TierConfigError("`channels` must be a mapping of channel -> {ladder: [...]}")

    policies: dict[Channel, ChannelPolicy] = {}
    for name, spec in raw_channels.items():
        channel = _channel(name)
        if not isinstance(spec, dict):
            raise TierConfigError(f"channel {name!r} must be a mapping")
        ladder = spec.get("ladder")
        if not ladder or not isinstance(ladder, list):
            raise TierConfigError(f"channel {name!r} needs a non-empty `ladder` list")
        unknown = [t for t in ladder if t not in KNOWN_TIERS]
        if unknown:
            raise TierConfigError(
                f"channel {name!r} ladder has unknown tier(s) {unknown}; "
                f"expected from {sorted(KNOWN_TIERS)}"
            )
        enabled = spec.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TierConfigError(f"channel {name!r} `enabled` must be true or false")
        max_concurrent = spec.get("max_concurrent", 0)
        if not isinstance(max_concurrent, int) or isinstance(max_concurrent, bool):
            raise TierConfigError(f"channel {name!r} `max_concurrent` must be an integer")
        if max_concurrent < 0:
            raise TierConfigError(f"channel {name!r} `max_concurrent` must not be negative")
        policies[channel] = ChannelPolicy(
            ladder=tuple(ladder), enabled=enabled, max_concurrent=max_concurrent
        )

    admission = data.get("admission") or {}
    max_oss = admission.get("max_oss_sessions", 0) if isinstance(admission, dict) else 0
    if not isinstance(max_oss, int) or isinstance(max_oss, bool) or max_oss < 0:
        raise TierConfigError("`admission.max_oss_sessions` must be a non-negative integer")

    # A private share larger than the whole box is not a cap, it is a typo that
    # reads as one. Refused here so it cannot be published.
    if max_oss:
        for channel, policy in policies.items():
            if policy.max_concurrent > max_oss:
                raise TierConfigError(
                    f"channel {channel.value!r} reserves {policy.max_concurrent} local voice "
                    f"seats but the box has {max_oss}"
                )

    return TierConfig(policies, max_oss, _parse_mix(data.get("campaign")))


def _channel(name: Any) -> Channel:
    try:
        channel = Channel(name)
    except (ValueError, TypeError) as exc:
        raise TierConfigError(
            f"unknown channel {name!r}; expected one of {[c.value for c in SWITCHABLE]}"
        ) from exc
    if channel not in SWITCHABLE:
        raise TierConfigError(
            f"channel {name!r} has no entry point to open or close; "
            f"expected one of {[c.value for c in SWITCHABLE]}"
        )
    return channel


def _parse_mix(campaign: Any) -> dict[Channel, int]:
    """`campaign.mix` — the one place a percentage is a real instruction (doc 12 §1.2).

    Empty is valid and means "no mix": the campaign calls whoever it can, exactly
    as it did before S-GL.1. A non-empty mix must sum to 100, because a mix that
    sums to 70 silently drops three patients in ten from tomorrow's outreach.
    """
    if campaign is None:
        return {}
    if not isinstance(campaign, dict):
        raise TierConfigError("`campaign` must be a mapping")
    raw = campaign.get("mix") or {}
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise TierConfigError("`campaign.mix` must be a mapping of channel -> percent")

    mix: dict[Channel, int] = {}
    for name, pct in raw.items():
        channel = _channel(name)
        if not isinstance(pct, int) or isinstance(pct, bool) or pct < 0 or pct > 100:
            raise TierConfigError(f"campaign mix for {name!r} must be a percent 0-100")
        mix[channel] = pct
    total = sum(mix.values())
    if total != 100:
        raise TierConfigError(f"`campaign.mix` must sum to 100, got {total}")
    return mix


def load_tier_config(path: Path | None = None) -> TierConfig:
    """Read and validate `config/tiers.yaml`."""
    path = path or CONFIG_PATH
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        raise TierConfigError(f"tiers config not found at {path}") from exc
    config = parse_tier_config(data)
    logger.info(
        "tier ladders: %s", {c.value: list(ladder) for c, ladder in config.channels.items()}
    )
    return config


@lru_cache
def get_tier_config() -> TierConfig:
    """The process-wide tier config, loaded once."""
    return load_tier_config()
