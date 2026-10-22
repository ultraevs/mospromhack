from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

# rtsp по tcp без буфера, иначе задержка копится
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay")

import cv2  # noqa: E402

log = logging.getLogger("worker.capture")

RECONNECT_PAUSES = (1, 2, 4, 8, 15)
ONLINE_TIMEOUT_S = 3.0


class Capture(Protocol):
    def read(self) -> tuple[bool, np.ndarray | None]: ...
    def release(self) -> None: ...


def open_rtsp(url: str) -> Capture | None:
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                                                 cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
    if not cap.isOpened():
        cap.release()
        return None
    return cap


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    ts_ms: int  # время захвата
    seq: int


class CameraReader:
    def __init__(self, camera_id: int, url: str, open_fn: Callable[[str], Capture | None] = open_rtsp,
                 pauses: tuple[float, ...] = RECONNECT_PAUSES):
        self.camera_id = camera_id
        self.url = url
        self.error: str | None = "ещё не подключена"
        self._open = open_fn
        self._pauses = pauses
        self._frame: Frame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._times: deque[float] = deque(maxlen=120)
        self._last_read = 0.0
        self._thread = threading.Thread(target=self._run, name=f"cam-{camera_id}", daemon=True)

    def start(self) -> "CameraReader":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=7)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frame

    @property
    def online(self) -> bool:
        return bool(self._last_read) and time.monotonic() - self._last_read < ONLINE_TIMEOUT_S

    def read_fps(self) -> float:
        now = time.monotonic()
        recent = [t for t in self._times if now - t <= 5.0]
        return round(len(recent) / 5.0, 1) if self.online else 0.0

    # храним только последний кадр, старые никому не нужны
    def _run(self) -> None:
        attempt = 0
        seq = 0
        while not self._stop.is_set():
            cap = None
            try:
                cap = self._open(self.url)
            except Exception as e:  # noqa: BLE001
                self.error = f"ошибка открытия потока: {e}"
            if cap is None:
                self.error = self.error or "поток не открывается"
                pause = self._pauses[min(attempt, len(self._pauses) - 1)]
                if attempt == 0 or attempt == len(self._pauses) - 1:
                    log.error("камера %s: нет потока %s, повтор через %s с", self.camera_id, self.url, pause)
                attempt += 1
                self._stop.wait(pause)
                self.error = "поток не открывается"
                continue

            log.info("камера %s: поток открыт", self.camera_id)
            while not self._stop.is_set():
                try:
                    ok, image = cap.read()
                except Exception as e:  # noqa: BLE001 - поток камеры не должен умирать молча
                    self.error = f"ошибка чтения: {e}"
                    log.exception("камера %s: ошибка чтения, переподключение", self.camera_id)
                    break
                if not ok or image is None:
                    self.error = "поток оборвался"
                    log.error("камера %s: поток оборвался, переподключение", self.camera_id)
                    break
                seq += 1
                now = time.monotonic()
                with self._lock:
                    self._frame = Frame(image, int(time.time() * 1000), seq)
                self._times.append(now)
                self._last_read = now
                self.error = None
                attempt = 0
            cap.release()
            if not self._stop.is_set():
                self._stop.wait(self._pauses[0])
