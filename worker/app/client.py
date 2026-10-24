from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime

import httpx
from websockets.sync.client import connect

from shared.schemas import Event, EventCreate, WorkerConfig

log = logging.getLogger("worker.client")


class BackendClient:
    def __init__(self, base_url: str, token: str, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, headers={"X-Internal-Token": token}, timeout=timeout)

    def get_config(self) -> WorkerConfig:
        r = self._http.get("/internal/config")
        r.raise_for_status()
        return WorkerConfig.model_validate(r.json())

    def post_event(self, body: EventCreate, snapshot: bytes | None = None, clean: bytes | None = None) -> Event:
        files = {name: (f"{name}.jpg", data, "image/jpeg") for name, data in (("snapshot", snapshot), ("clean", clean)) if data}
        if files:
            r = self._http.post("/internal/events", data={"event": body.model_dump_json()}, files=files)
        else:
            r = self._http.post("/internal/events", content=body.model_dump_json(),
                                headers={"Content-Type": "application/json"})
        r.raise_for_status()
        return Event.model_validate(r.json())

    def upload_clip(self, event_id: str, path) -> None:
        with open(path, "rb") as f:
            r = self._http.post(f"/internal/events/{event_id}/clip", files={"clip": ("clip.mp4", f, "video/mp4")},
                                timeout=30.0)
        r.raise_for_status()

    def upload_clip_anon(self, event_id: str, path) -> None:
        with open(path, "rb") as f:
            r = self._http.post(f"/internal/events/{event_id}/clip", params={"anon": "true"},
                                files={"clip": ("clip_anon.mp4", f, "video/mp4")}, timeout=30.0)
        r.raise_for_status()

    def close_event(self, event_id: str, ended_at: datetime) -> None:
        r = self._http.patch(f"/internal/events/{event_id}",
                             json={"status": "closed", "ended_at": ended_at.isoformat()})
        r.raise_for_status()

    def post_stats(self, stats: dict) -> None:
        self._http.post("/internal/stats", json=stats).raise_for_status()

    def close(self) -> None:
        self._http.close()


class DetectionsSender:

    def __init__(self, base_url: str, token: str, max_queue: int = 64):
        self.url = base_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://") + "/internal/detections"
        self._token = token
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max_queue)  # при обрыве выкидываем старое, рамки старше секунды бесполезны
        self._stop = threading.Event()
        self.connected = False
        self._thread = threading.Thread(target=self._run, name="detections-ws", daemon=True)

    def start(self) -> "DetectionsSender":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def send(self, text: str) -> None:
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(text)

    def _run(self) -> None:
        warned = False
        while not self._stop.is_set():
            try:
                with connect(self.url, additional_headers={"X-Internal-Token": self._token}, open_timeout=3) as ws:
                    self.connected, warned = True, False
                    log.info("подключён к %s", self.url)
                    while not self._stop.is_set():
                        try:
                            text = self._queue.get(timeout=0.5)
                        except queue.Empty:
                            continue
                        ws.send(text)
            except Exception as e:  # noqa: BLE001
                if not warned:
                    log.error("нет связи с %s: %s; повторяю", self.url, e)
                    warned = True
            self.connected = False
            self._stop.wait(1.0)
