import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { EventMessage, SafetyEvent } from "../types";
import { useSocket } from "./useSocket";

export function applyEventMessage(list: SafetyEvent[], msg: EventMessage, limit = 100): SafetyEvent[] {
  const i = list.findIndex((e) => e.id === msg.event.id);
  if (i >= 0) return list.map((e, k) => (k === i ? msg.event : e));
  return msg.type === "event_created" ? [msg.event, ...list].slice(0, limit) : list;
}

export function useEvents(cameraId?: number, limit = 50) {
  const [events, setEvents] = useState<SafetyEvent[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.events({ camera_id: cameraId, limit })
      .then((page) => { setEvents(page.items); setError(""); })
      .catch((e: Error) => setError(e.message));
  }, [cameraId, limit]);

  useEffect(load, [load]);

  const connected = useSocket<EventMessage>("/ws/events", (msg) => {
    if (cameraId !== undefined && msg.event.camera_id !== cameraId) return;
    setEvents((list) => applyEventMessage(list, msg, limit));
  });

  useEffect(() => { if (connected) load(); }, [connected, load]);

  return { events, connected, error };
}
