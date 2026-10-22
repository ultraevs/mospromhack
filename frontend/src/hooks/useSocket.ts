import { useEffect, useRef, useState } from "react";
import { wsUrl } from "../api";

export function useSocket<T>(path: string | null, onMessage: (msg: T) => void): boolean {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onMessage);
  handler.current = onMessage;

  useEffect(() => {
    if (!path) return;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const open = () => {
      ws = new WebSocket(wsUrl(path));
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        try {
          handler.current(JSON.parse(e.data) as T);
        } catch {
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = setTimeout(open, 1500);
      };
      ws.onerror = () => ws?.close();
    };
    open();

    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [path]);

  return connected;
}
