from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np

log = logging.getLogger("worker.evidence")

JPEG_QUALITY_BUFFER = 80
JPEG_QUALITY_SNAPSHOT = 90
VIOLATOR_COLOR = (59, 158, 245)
ZONE_COLOR = (79, 82, 240)


def resize_to_width(image: np.ndarray, width: int) -> np.ndarray:
    h, w = image.shape[:2]
    if w <= width:
        return image
    return cv2.resize(image, (width, round(h * width / w) // 2 * 2), interpolation=cv2.INTER_AREA)


def encode_jpeg(image: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("не удалось закодировать JPEG")
    return buf.tobytes()


def annotate(image: np.ndarray, bbox: Sequence[float] | None, label: str,
             polygon: Sequence[Sequence[float]] | None = None) -> np.ndarray:
    out = image.copy()
    h, w = out.shape[:2]
    thick = max(2, round(w / 480))
    if polygon and len(polygon) >= 3:
        pts = np.array([[round(x * w), round(y * h)] for x, y in polygon], np.int32)
        cv2.polylines(out, [pts], True, ZONE_COLOR, thick)
    if bbox:
        x1, y1 = round(bbox[0] * w), round(bbox[1] * h)
        x2, y2 = round((bbox[0] + bbox[2]) * w), round((bbox[1] + bbox[3]) * h)
        cv2.rectangle(out, (x1, y1), (x2, y2), VIOLATOR_COLOR, thick + 1)
    else:
        x1, y1 = thick * 4, thick * 16
    scale = max(0.6, w / 1600)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    ty = y1 - 6 if y1 - th - 10 > 0 else y1 + th + 10
    cv2.rectangle(out, (x1, ty - th - 6), (x1 + tw + 8, ty + 4), VIOLATOR_COLOR, -1)
    cv2.putText(out, label, (x1 + 4, ty), cv2.FONT_HERSHEY_SIMPLEX, scale, (22, 17, 14), thick, cv2.LINE_AA)
    return out


def build_clip(frames: Sequence[tuple[int, bytes]], out_path: Path) -> None:
    if len(frames) < 2:
        raise ValueError("для клипа нужно хотя бы два кадра")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg не найден в PATH")
    duration = (frames[-1][0] - frames[0][0]) / 1000
    fps = max(1.0, min(30.0, (len(frames) - 1) / duration if duration > 0 else 6.0))
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-framerate", f"{fps:.3f}",
           "-c:v", "mjpeg", "-i", "pipe:0", "-c:v", "libx264", "-preset", "veryfast", "-bf", "0",
           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.run(cmd, input=b"".join(jpeg for _, jpeg in frames), capture_output=True)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg завершился с кодом {proc.returncode}: {proc.stderr.decode(errors='replace')[-300:]}")


@dataclass
class _ClipJob:
    key: int
    start_ms: int
    end_ms: int


class CameraEvidence:

    def __init__(self, pre_seconds: float, post_seconds: float, clip_width: int, pool: ThreadPoolExecutor):
        self.pre_ms = int(pre_seconds * 1000)
        self.post_ms = int(post_seconds * 1000)
        self.clip_width = clip_width
        self._pool = pool
        self._frames: deque[tuple[int, bytes]] = deque()
        self._newest_ms = 0
        self._lock = threading.Lock()
        self._jobs: list[_ClipJob] = []

    def add_frame(self, image: np.ndarray, ts_ms: int) -> None:
        self._pool.submit(self._encode, image, ts_ms)

    def _encode(self, image: np.ndarray, ts_ms: int) -> None:
        try:
            jpeg = encode_jpeg(resize_to_width(image, self.clip_width), JPEG_QUALITY_BUFFER)
        except Exception:  # noqa: BLE001
            log.exception("не удалось закодировать кадр для буфера")
            return
        with self._lock:
            self._frames.append((ts_ms, jpeg))
            self._newest_ms = max(self._newest_ms, ts_ms)
            keep_from = min([self._newest_ms - self.pre_ms] + [j.start_ms for j in self._jobs])
            if self._frames[0][0] < keep_from:
                self._frames = deque(f for f in self._frames if f[0] >= keep_from)

    def start_clip(self, key: int, event_ms: int) -> None:
        with self._lock:
            self._jobs.append(_ClipJob(key, event_ms - self.pre_ms, event_ms + self.post_ms))

    def ready_clips(self, now_ms: int) -> list[tuple[int, list[tuple[int, bytes]]]]:
        out = []
        with self._lock:
            for job in [j for j in self._jobs if now_ms >= j.end_ms]:
                self._jobs.remove(job)
                frames = sorted(f for f in self._frames if job.start_ms <= f[0] <= job.end_ms)
                out.append((job.key, frames))
        return out

    def pending(self) -> int:
        with self._lock:
            return len(self._jobs)


class ClipBuilder:

    def __init__(self, pool: ThreadPoolExecutor, on_done: Callable[[int, Path | None], None],
                 build: Callable[[Sequence[tuple[int, bytes]], Path], None] = build_clip):
        self._pool = pool
        self._on_done = on_done
        self._build = build
        self._tmp = Path(tempfile.mkdtemp(prefix="clips-"))

    def submit(self, key: int, frames: list[tuple[int, bytes]]) -> None:
        self._pool.submit(self._run, key, frames)

    def _run(self, key: int, frames: list[tuple[int, bytes]]) -> None:
        path = self._tmp / f"{key}.mp4"
        try:
            self._build(frames, path)
        except Exception as e:  # noqa: BLE001
            log.error("клип события %s не собран: %s", key, e)
            self._on_done(key, None)
            return
        self._on_done(key, path)

    def cleanup(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)
