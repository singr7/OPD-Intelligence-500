"""Kokoro TTS micro-service — the `local_tts` engine for the kiosk read-aloud.

A thin FastAPI wrapper that speaks the exact contract our `LocalTTSProvider`
already calls (doc 10 §6, backend/app/providers/local_oss/tts.py):

    POST /tts  {text, voice?, language?, sample_rate?}  ->  {"audio": "<base64 wav>"}

It runs Kokoro-82M (Apache-2.0, ~0.3 GB VRAM, RTF≈0.1) on the GPU box as a peer
of opd-vllm / opd-stt — the same "one container on opd_default, reached by name"
pattern (doc 10 §2), NOT a compose service, so `docker compose down` can never
take the voice down with the app.

This is the "default voice now" path. Voicebox voice-cloning (the branded Dhara
identity) is the reserved later iteration — swapping to it is a config change
(TTS_PROVIDER=voicebox), with no change here.
"""

from __future__ import annotations

import base64
import io
import logging

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from kokoro import KPipeline
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tts-kokoro")

# Kokoro emits 24 kHz float32 mono. The kiosk asks for 24 kHz too (doc 10 §6),
# so there is no resample on the hot path.
KOKORO_SAMPLE_RATE = 24000

# Our Lang -> Kokoro language code. Kokoro codes are single letters; we support
# the two the pilot reads aloud. mr/te are not Kokoro languages — they 400 here
# so the provider chain / browser voice takes them (the bake-off is where Indic
# languages get a real engine, doc 08 §6).
LANG_CODE = {"en": "a", "hi": "h"}  # a = American English, h = Hindi

# A sensible default voice per language. VERIFY on the box: Kokoro loads voices
# by id from its bundled voice set; these are the common ones but confirm with
# GET /voices (and the Kokoro voice list) before pinning LOCAL_TTS_VOICE. A blank
# or unknown voice falls back to these.
DEFAULT_VOICE = {"a": "af_heart", "h": "hf_alpha"}

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
logger.info("kokoro tts starting on %s", DEVICE)

# One pipeline per language, built lazily and cached — a pipeline holds the model
# + G2P for its language; rebuilding per request would throw away the load each time.
_pipelines: dict[str, KPipeline] = {}


def _pipeline(code: str) -> KPipeline:
    if code not in _pipelines:
        # No explicit device kwarg: Kokoro auto-selects CUDA when available, which
        # keeps this working across kokoro versions (the kwarg name has moved).
        _pipelines[code] = KPipeline(lang_code=code)
    return _pipelines[code]


def _lang_code(language: str | None) -> str:
    # Accept "hi", "hi-IN", "en", "en-IN" — take the 2-letter prefix.
    prefix = (language or "hi").split("-")[0].lower()
    code = LANG_CODE.get(prefix)
    if code is None:
        raise HTTPException(
            status_code=400,
            detail=f"kokoro tts does not support language {prefix!r} (supported: en, hi)",
        )
    return code


def _resolve_voice(code: str, requested: str | None) -> str:
    """Pick a Kokoro voice valid for this language.

    Kokoro voice ids are `<lang><gender>_<name>` — the first letter is the language
    (`hf_alpha` = Hindi, `af_heart` = American English). The backend adapter sends
    ONE voice id for every language (and injects its own `dhara_hi_v1` default when
    LOCAL_TTS_VOICE is blank), so a request's `voice` only applies when its language
    letter matches this pipeline; otherwise use the language default. This is why
    leaving LOCAL_TTS_VOICE blank is the clean choice: the container picks the right
    voice per language (hi -> hf_alpha, en -> af_heart) with no wasted synth.
    """
    if requested and requested[:1] == code:
        return requested
    return DEFAULT_VOICE[code]


def _synth(code: str, text: str, voice: str) -> np.ndarray | None:
    """Synthesize one utterance to a float32 array, or None if this voice fails."""
    try:
        # Kokoro yields (graphemes, phonemes, audio) per chunk; concatenate the
        # audio for the full utterance.
        chunks = [audio for _, _, audio in _pipeline(code)(text, voice=voice)]
    except Exception:  # noqa: BLE001 - a bad voice id / G2P miss is recoverable
        logger.exception("synthesis failed for voice=%s", voice)
        return None
    if not chunks:
        return None
    return np.concatenate([np.asarray(c, dtype=np.float32) for c in chunks])


app = FastAPI(title="opd-tts (kokoro)")


class TtsIn(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None
    language: str | None = "hi"
    sample_rate: int | None = KOKORO_SAMPLE_RATE
    model: str | None = None  # accepted + ignored (single engine here)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "device": DEVICE, "languages": sorted(LANG_CODE)}


@app.get("/voices")
def voices() -> dict:
    """The per-language default voices this service uses. Confirm a voice id here
    before pinning LOCAL_TTS_VOICE."""
    return {
        "defaults": DEFAULT_VOICE,
        "note": "any Kokoro voice id valid for the language works; blank uses the default",
    }


@app.post("/tts")
def tts(body: TtsIn) -> dict:
    code = _lang_code(body.language)
    voice = _resolve_voice(code, body.voice)

    audio = _synth(code, body.text, voice)
    if audio is None and voice != DEFAULT_VOICE[code]:
        # A stale / wrong voice id (e.g. a Voicebox clone name pointed at Kokoro)
        # should not silence the kiosk — fall back to the language default.
        logger.warning("voice %r failed; falling back to %s", voice, DEFAULT_VOICE[code])
        audio = _synth(code, body.text, DEFAULT_VOICE[code])
    if audio is None:
        raise HTTPException(status_code=500, detail="kokoro synthesis failed")

    # float32 [-1,1] -> 16-bit PCM WAV in memory -> base64, matching the adapter's
    # AudioClip.from_b64(..., mime="audio/wav") expectation.
    buf = io.BytesIO()
    sf.write(buf, audio, KOKORO_SAMPLE_RATE, subtype="PCM_16", format="WAV")
    return {"audio": base64.b64encode(buf.getvalue()).decode("ascii")}
