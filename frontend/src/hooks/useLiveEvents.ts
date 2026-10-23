import { useCallback, useEffect, useRef, useState } from "react";
import type { EventMessage } from "../types";
import { wsUrl } from "../api";

export function useLiveEvents(onMessage: (message: EventMessage) => void, enabled = true) {
  const callbackRef = useRef(onMessage);
  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  const heartbeatRef = useRef<number | null>(null);
  const reconnectRef = useRef(true);
  const [connected, setConnected] = useState(false);
  const [lastSync, setLastSync] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);

  callbackRef.current = onMessage;

  const connect = useCallback(() => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    if (heartbeatRef.current !== null) window.clearInterval(heartbeatRef.current);
    reconnectRef.current = true;
    socketRef.current?.close();
    const socket = new WebSocket(wsUrl("/ws/events"));
    socketRef.current = socket;
    socket.onopen = () => {
      if (socketRef.current !== socket) return;
      setConnected(true);
      setAttempt(0);
      setLastSync(Date.now());
      heartbeatRef.current = window.setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send("ping");
      }, 20_000);
    };
    socket.onmessage = (event) => {
      if (socketRef.current !== socket) return;
      try {
        callbackRef.current(JSON.parse(event.data) as EventMessage);
        setLastSync(Date.now());
      } catch {
        // Ignore malformed frames; the next REST refresh remains authoritative.
      }
    };
    socket.onclose = (event) => {
      if (socketRef.current !== socket) return;
      if (heartbeatRef.current !== null) {
        window.clearInterval(heartbeatRef.current);
        heartbeatRef.current = null;
      }
      setConnected(false);
      if (event.code === 4401 || event.code === 4403) {
        reconnectRef.current = false;
        window.dispatchEvent(new Event("auth-expired"));
        return;
      }
      if (!reconnectRef.current) return;
      setAttempt((current) => {
        const next = current + 1;
        timerRef.current = window.setTimeout(connect, Math.min(15_000, 1_000 * 2 ** Math.min(next, 4)));
        return next;
      });
    };
    socket.onerror = () => socket.close();
  }, []);

  useEffect(() => {
    if (!enabled) {
      reconnectRef.current = false;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      if (heartbeatRef.current !== null) window.clearInterval(heartbeatRef.current);
      socketRef.current?.close();
      setConnected(false);
      return;
    }
    connect();
    return () => {
      reconnectRef.current = false;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      if (heartbeatRef.current !== null) window.clearInterval(heartbeatRef.current);
      socketRef.current?.close();
    };
  }, [connect, enabled]);

  return { connected, lastSync, attempt, reconnect: connect };
}
