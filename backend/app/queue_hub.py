"""In-process fan-out for the live queue + the downtime flag (doc 03 §6, 01 §5).

The board and the coordinator console are open all day; a token confirmed on a
kiosk must appear on both **without a refresh** (S8 AC). Rather than have each
screen poll, they hold a WebSocket to `/queue/ws` and this hub pushes a tiny
event when anything changes; the client then re-fetches the snapshot it wants
(board vs. console are different shapes, so pushing the full state to both would
be wasteful and coupled — the event is just a "something moved, come look").

## Why in-process, and what that costs

The pilot runs one `api` container (doc 02 §1), so a set of connections in memory
is the whole fan-out. A second replica would each hold their own connections and
miss each other's events — the same single-process limitation the cost-guard
override store and the OSS `AdmissionController` carry, and the same fix (a Redis
pub/sub channel) when horizontal scale arrives (backlog, S19/S20). Documented so
it is a known edge, not a surprise.

## Downtime is a broadcast flag, not a row

"Downtime mode" is the coordinator flipping the whole floor to paper-with-a-memory
(doc 01 §5). It is deliberately **ephemeral, in-memory state**: it describes "is
the OPD in a drill / outage right now", which is a live operational fact, not a
clinical record — and it must be settable *while the database write path is the
very thing that is down*. Persisting it would put the downtime switch behind the
thing downtime exists to survive. It resets to "up" on an api restart, which is
correct: a restart is recovery.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger(__name__)


class QueueHub:
    """Broadcasts queue-change events to connected board/console sockets."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._downtime: bool = False
        self._downtime_since: datetime | None = None

    # -- membership -----------------------------------------------------------

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        # Send the current downtime state on connect so a screen that joins mid-
        # outage shows the banner immediately, not only on the next transition.
        await self._send(ws, self.downtime_event())

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # -- downtime -------------------------------------------------------------

    @property
    def downtime(self) -> bool:
        return self._downtime

    @property
    def downtime_since(self) -> datetime | None:
        return self._downtime_since

    def downtime_event(self) -> dict:
        return {
            "type": "downtime",
            "active": self._downtime,
            "since": self._downtime_since.isoformat() if self._downtime_since else None,
        }

    async def set_downtime(self, active: bool) -> dict:
        """Flip downtime and broadcast it. Idempotent; returns the new event."""
        if active and not self._downtime:
            self._downtime_since = datetime.now(UTC)
        elif not active:
            self._downtime_since = None
        self._downtime = active
        event = self.downtime_event()
        await self.broadcast(event)
        return event

    # -- fan-out --------------------------------------------------------------

    async def notify_queue_changed(self) -> None:
        """Tell every screen the queue moved — they re-fetch their snapshot."""
        await self.broadcast({"type": "queue_update", "at": datetime.now(UTC).isoformat()})

    async def broadcast(self, message: dict) -> None:
        # Copy under lock, send outside it: a slow/dead socket must not hold up
        # the others, and `_send` mutates `_clients` on failure.
        async with self._lock:
            targets = list(self._clients)
        for ws in targets:
            await self._send(ws, message)

    async def _send(self, ws: WebSocket, message: dict) -> None:
        if ws.application_state != WebSocketState.CONNECTED:
            await self.disconnect(ws)
            return
        try:
            await ws.send_json(message)
        except Exception:  # noqa: BLE001 - a dropped client must not break fan-out
            logger.debug("dropping a disconnected queue socket", exc_info=True)
            await self.disconnect(ws)
