import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "../api";
import type { EventMessage } from "../types";

export interface LiveState {
  connected: boolean;
  lastSync: number | null;
  attempt: number;
  reconnect: () => void;
}

export function useLiveEvents(onMessage: (message: EventMessage) => void, enabled = true): LiveState {
  const callbackRef = useRef(onMessage);
  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  const heartbeatRef = useRef<number | null>(null);
  const reconnectRef = useRef(true);
  const [connected, setConnected] = useState(false);
  const [lastSync, setLastSync] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);

  callbackRef.current = onMessage;

  const stopTimers = () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    if (heartbeatRef.current !== null) window.clearInterval(heartbeatRef.current);
    timerRef.current = null;
    heartbeatRef.current = null;
  };

  const connect = useCallback(() => {
    stopTimers();
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
        // битый кадр пропускаем, rest все равно перечитается
      }
    };
    socket.onclose = (event) => {
      if (socketRef.current !== socket) return;
      if (heartbeatRef.current !== null) window.clearInterval(heartbeatRef.current);
      heartbeatRef.current = null;
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
    if (!enabled) return;
    connect();
    return () => {
      reconnectRef.current = false;
      stopTimers();
      socketRef.current?.close();
      setConnected(false);
    };
  }, [connect, enabled]);

  return { connected, lastSync, attempt, reconnect: connect };
}
