"""voice-gw service — a separate deployable from `api` so a telephony crash never
takes down HTTP (doc 05 §3).

S1 shipped the health route + app factory. S14 lands the Exotel Voicebot websocket
bridge: `WS /exotel/voicebot` runs one phone intake (`gw.call.handle_call`) over the
shared `IntakeEngine`, stood up in-process on the lifespan (`gw.engine`). The V1
Gemini Live path and the V2 STT↔TTS path both live in that engine; this service is
the phone channel adapter over it.

S15 adds the second number: `WS /exotel/receptionist` runs the appointment line
(`gw.reception.handle_receptionist_call`) over `app.receptionist`. Two applets, two
paths, one service — an inbound caller is either doing an intake or managing an
appointment, and Exotel decides which by pointing its applet at one URL or the other.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.config import Settings, get_settings
from gw import __version__
from gw.call import build_phonecall_store, handle_call
from gw.engine import build_lifespan, get_intake_engine, get_sessionmaker
from gw.exotel import ExotelTransport
from gw.reception import handle_receptionist_call


class _WebSocketTransport:
    """Adapts a Starlette `WebSocket` to `gw.exotel.ExotelTransport` — JSON text
    frames in and out. The call driver never touches the socket directly, so the same
    driver runs over this and over the in-memory fake replay client."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send(self, frame: dict) -> None:
        await self._ws.send_json(frame)

    async def receive(self) -> dict | None:
        try:
            return await self._ws.receive_json()
        except WebSocketDisconnect:
            return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="OPD Voice Gateway",
        version=__version__,
        lifespan=build_lifespan(settings),
    )
    app.state.settings = settings
    app.state.phonecall_store = build_phonecall_store(settings)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "voice-gw", "version": __version__}

    @app.websocket("/exotel/voicebot")
    async def voicebot(ws: WebSocket) -> None:
        """One phone call. Exotel's Voicebot Applet connects here and streams audio;
        we run the intake and stream audio back until the caller hangs up."""
        await ws.accept()
        transport: ExotelTransport = _WebSocketTransport(ws)
        try:
            await handle_call(
                transport,
                engine=get_intake_engine(ws),
                sessionmaker=get_sessionmaker(ws),
                settings=ws.app.state.settings,
                phonecall_store=ws.app.state.phonecall_store,
            )
        except (WebSocketDisconnect, ConnectionError):
            # A dropped call is normal — the driver already saved a partial per turn.
            pass
        finally:
            try:
                await ws.close()
            except RuntimeError:
                pass

    @app.websocket("/exotel/receptionist")
    async def receptionist(ws: WebSocket) -> None:
        """One inbound appointment call (doc 03 §2). Same wire protocol as the
        intake socket; a different conversation on the other side of it."""
        await ws.accept()
        transport: ExotelTransport = _WebSocketTransport(ws)
        try:
            await handle_receptionist_call(
                transport,
                sessionmaker=get_sessionmaker(ws),
                settings=ws.app.state.settings,
            )
        except (WebSocketDisconnect, ConnectionError):
            # A caller who hangs up mid-sentence: anything already booked was
            # committed on that turn.
            pass
        finally:
            try:
                await ws.close()
            except RuntimeError:
                pass

    return app


app = create_app()
