from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger("backend.hub")


class Hub:
    def __init__(self) -> None:
        self._detections: dict[int, set[WebSocket]] = defaultdict(set)
        self._events: set[WebSocket] = set()

    def subscribe_detections(self, camera_id: int, ws: WebSocket) -> None:
        self._detections[camera_id].add(ws)

    def unsubscribe_detections(self, camera_id: int, ws: WebSocket) -> None:
        self._detections[camera_id].discard(ws)

    def subscribe_events(self, ws: WebSocket) -> None:
        self._events.add(ws)

    def unsubscribe_events(self, ws: WebSocket) -> None:
        self._events.discard(ws)

    async def _send(self, targets: set[WebSocket], text: str) -> None:
        for ws in list(targets):
            try:
                await ws.send_text(text)
            except Exception as e:  # noqa: BLE001 - клиент отвалился, убираем из подписчиков
                log.info("подписчик WebSocket отключён: %s", e)
                targets.discard(ws)

    async def send_detections(self, camera_id: int, text: str) -> None:
        if self._detections.get(camera_id):
            await self._send(self._detections[camera_id], text)

    async def send_event(self, text: str) -> None:
        await self._send(self._events, text)

    def counts(self) -> dict[str, int]:
        return {"detections": sum(len(v) for v in self._detections.values()), "events": len(self._events)}
