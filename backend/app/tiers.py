"""Per-channel tier ladder + admission config (doc 08 §3/§5).

Loads `config/tiers.yaml` — the data that says, per channel, which tiers to try
in what order (`ladder`) and how many concurrent local voice sessions the one GPU
may hold (`admission.max_oss_sessions`). Validated on load so a typo'd channel or
an unknown tier label fails loudly at boot, where it is cheap, rather than
silently routing a channel to nowhere at 9am.

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


class TierConfigError(ValueError):
    """`config/tiers.yaml` is malformed. Raised at load, never swallowed."""


class TierConfig:
    """The validated contents of `config/tiers.yaml`."""

    def __init__(self, ladders: dict[Channel, tuple[str, ...]], max_oss_sessions: int) -> None:
        self._ladders = ladders
        self.max_oss_sessions = max_oss_sessions

    def ladder_for(self, channel: Channel) -> tuple[str, ...]:
        """The ordered tier preference for a channel, best first.

        A channel with no explicit ladder falls back to cloud→zero-AI (`v2, v3`)
        rather than raising: a new channel that forgot a ladder entry should keep
        working on the safe cloud path, not fail to start.
        """
        return self._ladders.get(channel, ("v2", "v3"))

    @property
    def channels(self) -> dict[Channel, tuple[str, ...]]:
        return dict(self._ladders)

    def admission_controller(self) -> AdmissionController:
        """An `AdmissionController` capped from `admission.max_oss_sessions`."""
        return AdmissionController({OSS_PROFILE: self.max_oss_sessions})


def parse_tier_config(data: dict[str, Any]) -> TierConfig:
    """Validate a raw yaml dict into a `TierConfig`. Pure — no file I/O."""
    if not isinstance(data, dict):
        raise TierConfigError("tiers config must be a mapping")

    raw_channels = data.get("channels") or {}
    if not isinstance(raw_channels, dict):
        raise TierConfigError("`channels` must be a mapping of channel -> {ladder: [...]}")

    ladders: dict[Channel, tuple[str, ...]] = {}
    for name, spec in raw_channels.items():
        try:
            channel = Channel(name)
        except ValueError as exc:
            raise TierConfigError(
                f"unknown channel {name!r}; expected one of {[c.value for c in Channel]}"
            ) from exc
        ladder = (spec or {}).get("ladder") if isinstance(spec, dict) else None
        if not ladder or not isinstance(ladder, list):
            raise TierConfigError(f"channel {name!r} needs a non-empty `ladder` list")
        unknown = [t for t in ladder if t not in KNOWN_TIERS]
        if unknown:
            raise TierConfigError(
                f"channel {name!r} ladder has unknown tier(s) {unknown}; "
                f"expected from {sorted(KNOWN_TIERS)}"
            )
        ladders[channel] = tuple(ladder)

    admission = data.get("admission") or {}
    max_oss = admission.get("max_oss_sessions", 0) if isinstance(admission, dict) else 0
    if not isinstance(max_oss, int) or max_oss < 0:
        raise TierConfigError("`admission.max_oss_sessions` must be a non-negative integer")

    return TierConfig(ladders, max_oss)


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
