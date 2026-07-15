"""Audio value types shared by STT, TTS, realtime voice and telephony.

Kept out of the provider modules because all four speak in these, and because
`duration_seconds` is a metering input — an audio clip that cannot say how long
it is cannot be priced (Sarvam and Google both bill STT by duration).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

#: Telephony-native format (doc 02 §2: Exotel Voicebot streams 8kHz PCM).
#: Kiosk/browser capture arrives as webm/opus and is transcoded at the edge.
PCM16 = "audio/l16"


@dataclass(frozen=True, slots=True)
class AudioClip:
    """A chunk of audio plus what a vendor needs to know about it.

    `duration_seconds` is optional because the browser does not always tell us;
    for raw PCM it is computable, and `duration()` does that rather than letting
    each provider re-derive it (differently, and wrongly, at 3am).
    """

    data: bytes
    mime: str = PCM16
    sample_rate: int = 8000
    channels: int = 1
    duration_seconds: Decimal | None = None

    BYTES_PER_SAMPLE: ClassVar[int] = 2  # 16-bit

    def duration(self) -> Decimal:
        """Seconds of audio. Exact for raw PCM, declared otherwise, 0 if unknown.

        Returning 0 rather than guessing for compressed formats is deliberate:
        an invented duration silently becomes an invented rupee amount on the
        S18 dashboard. A zero shows up as unpriced usage, which is visible.
        """
        if self.duration_seconds is not None:
            return self.duration_seconds
        if self.mime == PCM16 and self.sample_rate and self.channels:
            samples = len(self.data) / (self.BYTES_PER_SAMPLE * self.channels)
            return Decimal(samples) / Decimal(self.sample_rate)
        return Decimal("0")

    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    @classmethod
    def from_b64(cls, encoded: str, **kwargs) -> AudioClip:
        return cls(data=base64.b64decode(encoded), **kwargs)


#: Vendor language codes. Our `Lang` enum is bare ISO-639 (doc 02 §4); every
#: speech vendor wants the region tag, and India-specific voices only exist under
#: the -IN variants. One mapping, here, rather than four scattered f-strings.
LANG_TO_BCP47: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "mr": "mr-IN",
    "te": "te-IN",
}


def bcp47(lang: str) -> str:
    """`hi` → `hi-IN`. Unknown languages pass through untouched so a vendor's
    own error message (not a KeyError here) explains what it does not support."""
    return LANG_TO_BCP47.get(lang, lang)
