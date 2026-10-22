from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

# rtsp по tcp без буфера, иначе задержка копится
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay")

import cv2  # noqa: E402

log = logging.getLogger("worker.capture")

RECONNECT_PAUSES = (1, 2, 4, 8, 15)
ONLINE_TIMEOUT_S = 3.0
NEW_FRAMES_KEPT = 2


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


CUVID = {"h264": "h264_cuvid", "hevc": "hevc_cuvid"}
FIRST_FRAME_TIMEOUT_S = 15.0
_cuvid_failed = False


def probe_stream(url: str) -> tuple[str, int, int] | None:
    cmd = ["ffprobe", "-v", "error"]
    if url.startswith("rtsp"):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += ["-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        codec, w, h = out[0].split(",")[:3]
        return codec, int(w), int(h)
    except Exception:  # noqa: BLE001
        return None


def target_size(src_w: int, src_h: int, width: int) -> tuple[int, int]:
    if width <= 0 or src_w <= width:
        return src_w, src_h
    return width // 2 * 2, max(2, round(src_h * width / src_w) // 2 * 2)


def ffmpeg_command(url: str, codec: str, size: tuple[int, int], src: tuple[int, int], fps: float, cuvid: bool) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if url.startswith("rtsp"):
        cmd += ["-rtsp_transport", "tcp", "-fflags", "nobuffer", "-flags", "low_delay", "-timeout", "5000000"]
    filters = [f"fps={fps:g}"] if fps > 0 else []
    if cuvid:
        cmd += ["-c:v", CUVID[codec]]
        if size != src:
            cmd += ["-resize", f"{size[0]}x{size[1]}"]
    elif size != src:
        filters.append(f"scale={size[0]}:{size[1]}")
    cmd += ["-i", url, "-an"]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    return cmd + ["-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]


class FfmpegCapture:

    def __init__(self, url: str, codec: str, src: tuple[int, int], size: tuple[int, int], fps: float, cuvid: bool,
                 log_path: Path | None = None):
        self.size, self.src, self.cuvid = size, src, cuvid
        self.px_scale = src[1] / size[1]
        self._bytes = size[0] * size[1] * 3
        self._log = open(log_path, "wb") if log_path else subprocess.DEVNULL
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = subprocess.Popen(ffmpeg_command(url, codec, size, src, fps, cuvid), stdout=subprocess.PIPE,
                                      stderr=self._log, stdin=subprocess.DEVNULL, bufsize=0, creationflags=flags)

    def read(self) -> tuple[bool, np.ndarray | None]:
        image = np.empty((self.size[1], self.size[0], 3), np.uint8)
        view, got = memoryview(image).cast("B"), 0
        while got < self._bytes:
            n = self._proc.stdout.readinto(view[got:])
            if not n:
                return False, None
            got += n
        return True, image

    def interrupt(self) -> None:
        if self._proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"], capture_output=True)
        else:
            self._proc.kill()

    def release(self) -> None:
        self.interrupt()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        self._proc.stdout.close()
        if self._log is not subprocess.DEVNULL:
            self._log.close()


def open_ffmpeg(url: str, width: int, hwaccel: str, fps: float, log_path: Path | None = None) -> "Capture | None":
    global _cuvid_failed
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg и ffprobe не найдены в PATH")
    info = probe_stream(url)
    if info is None:
        return None
    codec, src_w, src_h = info
    size = target_size(src_w, src_h, width)
    for cuvid in ([True, False] if hwaccel == "auto" and codec in CUVID and not _cuvid_failed else [False]):
        cap = FfmpegCapture(url, codec, (src_w, src_h), size, fps, cuvid, log_path)
        timer = threading.Timer(FIRST_FRAME_TIMEOUT_S, cap.interrupt)
        timer.start()
        ok, image = cap.read()
        timer.cancel()
        if ok:
            return _WithFirst(cap, image)
        cap.release()
        if cuvid:
            _cuvid_failed = True
            log.warning("декодер видеокарты (%s) не запустился, камеры декодируются на процессоре", CUVID[codec])
    return None


class _WithFirst:

    def __init__(self, cap: FfmpegCapture, first: np.ndarray):
        self._cap, self._first = cap, first
        self.px_scale, self.cuvid, self.size, self.src = cap.px_scale, cap.cuvid, cap.size, cap.src

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._first is not None:
            image, self._first = self._first, None
            return True, image
        return self._cap.read()

    def interrupt(self) -> None:
        self._cap.interrupt()

    def release(self) -> None:
        self._cap.release()


def decoder_state() -> str:
    return "cpu" if _cuvid_failed else "auto"


def open_capture(url: str, width: int = 0, hwaccel: str = "none", fps: float = 0.0, backend: str = "ffmpeg",
                 log_path: Path | None = None) -> "Capture | None":
    if backend == "opencv":
        return open_rtsp(url)
    return open_ffmpeg(url, width, hwaccel, fps, log_path)


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    ts_ms: int  # время захвата
    seq: int
    px_scale: float = 1.0


class CameraReader:
    def __init__(self, camera_id: int, url: str, open_fn: Callable[[str], Capture | None] = open_rtsp,
                 pauses: tuple[float, ...] = RECONNECT_PAUSES):
        self.camera_id = camera_id
        self.url = url
        self.error: str | None = "ещё не подключена"
        self._open = open_fn
        self._pauses = pauses
        self._frame: Frame | None = None
        self._new: deque[Frame] = deque(maxlen=NEW_FRAMES_KEPT)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._times: deque[float] = deque(maxlen=120)
        self._last_read = 0.0
        self._cap: Capture | None = None
        self.decoder = ""
        self._thread = threading.Thread(target=self._run, name=f"cam-{camera_id}", daemon=True)

    def start(self) -> "CameraReader":
        self._thread.start()
        return self

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        interrupt = getattr(self._cap, "interrupt", None)
        if interrupt:
            interrupt()
        if wait:
            self._thread.join(timeout=7)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frame

    def take_new(self) -> list[Frame]:
        with self._lock:
            frames = list(self._new)
            self._new.clear()
            return frames

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

            self._cap = cap
            px_scale = float(getattr(cap, "px_scale", 1.0))
            if hasattr(cap, "cuvid"):
                self.decoder = "gpu" if cap.cuvid else "cpu"
            log.info("камера %s: поток открыт%s", self.camera_id,
                     f", декодирование {self.decoder}, кадр {cap.size[0]}x{cap.size[1]} из {cap.src[0]}x{cap.src[1]}"
                     if self.decoder else "")
            while not self._stop.is_set():
                try:
                    ok, image = cap.read()
                except Exception as e:  # noqa: BLE001
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
                    self._frame = Frame(image, int(time.time() * 1000), seq, px_scale)
                    self._new.append(self._frame)
                self._times.append(now)
                self._last_read = now
                self.error = None
                attempt = 0
            self._cap = None
            cap.release()
            if not self._stop.is_set():
                self._stop.wait(self._pauses[0])
