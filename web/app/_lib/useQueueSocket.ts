"use client";

// The live-sync WebSocket the board and console share (backend /queue/ws). It
// carries only change-pings and the downtime flag — no PII — so a wall TV holds
// it open with no login. The hook auto-reconnects (an OPD network blips), and
// the caller re-fetches its own snapshot on every "queue_update".

import { useEffect, useRef, useState } from "react";
import { WS_URL } from "./queue";

export type QueueEvent =
  | { type: "queue_update"; at: string }
  | { type: "downtime"; active: boolean; since: string | null };

type Options = {
  onEvent: (event: QueueEvent) => void;
  enabled?: boolean;
};

export function useQueueSocket({ onEvent, enabled = true }: Options): { connected: boolean } {
  const [connected, setConnected] = useState(false);
  // Keep the latest callback without re-opening the socket on every render.
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        try {
          onEventRef.current(JSON.parse(e.data) as QueueEvent);
        } catch {
          /* ignore a malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500); // reconnect after a blip
      };
      ws.onerror = () => ws?.close();
    };
    connect();

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [enabled]);

  return { connected };
}
