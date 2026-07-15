"""voice-gw service — separate deployable from `api` so a telephony crash never
takes down HTTP (doc 05 §3).

S1 ships only the health route + app factory. The Exotel Voicebot websocket
bridge and the V1 Gemini Live / V2 STT↔TTS relay land in S14.
"""

from fastapi import FastAPI

from gw import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="OPD Voice Gateway", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "voice-gw", "version": __version__}

    return app


app = create_app()
