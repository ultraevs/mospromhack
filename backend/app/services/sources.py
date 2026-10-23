from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from shared.sync import loop_input, loop_offset, probe_duration

log = logging.getLogger("backend.sources")

RESTART_PAUSES = (1, 2, 5, 10)


def path_name(camera_id: int) -> str:
    return f"cam-{camera_id}"


@dataclass(frozen=True)
# каждая камера = путь cam-N в mediamtx. файл крутим через ffmpeg, rtsp отдаем mediamtx напрямую
class Desired:
    camera_id: int
    kind: str  # file | rtsp
    source: str


@dataclass
class _Proc:
    desired: Desired
    popen: subprocess.Popen
    started: float
    log_file: object


def _double(rate: str) -> str:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", rate.strip())
    if not m:
        return rate
    n = float(m.group(1)) * 2
    return f"{int(n) if n.is_integer() else n}{m.group(2)}"


class SourceManager:
    def __init__(self, videos_dir: Path, logs_dir: Path, rtsp_port: int, api_port: int, active: bool = True,
                 host: str = "127.0.0.1", width: int = 0, maxrate: str = "", lag_s: float = 0.0,
                 codec: str = "h264"):
        self.width = width
        self.lag_s = lag_s
        self.maxrate = maxrate
        self.codec = codec or os.getenv("STREAM_CODEC", "h264")
        self.videos_dir = videos_dir
        self.logs_dir = logs_dir
        self.rtsp_port = rtsp_port
        self.host = host
        self.api = f"http://{host}:{api_port}"
        self.active = active  # в тестах False
        self._desired: dict[int, Desired] = {}
        self._procs: dict[int, _Proc] = {}
        self._configured: dict[int, str] = {}
        self._errors: dict[int, str] = {}
        self._fails: dict[int, int] = {}
        self._next_try: dict[int, float] = {}
        self._ready: set[str] = set()
        self._durations: dict[Path, float | None] = {}
        self.mediamtx_error: str | None = "ещё не проверялся"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None


    def apply(self, cameras: list[Desired]) -> None:
        with self._lock:
            self._desired = {c.camera_id: c for c in cameras}

    def start(self) -> None:
        if not self.active or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="sources", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        for cam_id in list(self._procs):
            self._kill(cam_id)

    def rtsp_url(self, camera_id: int) -> str:
        return f"rtsp://{self.host}:{self.rtsp_port}/{path_name(camera_id)}"

    def status(self) -> dict:
        with self._lock:
            cams = {}
            for cam_id, d in self._desired.items():
                proc = self._procs.get(cam_id)
                cams[str(cam_id)] = {
                    "kind": d.kind,
                    "publishing": path_name(cam_id) in self._ready,
                    "ffmpeg_running": bool(proc and proc.popen.poll() is None) if d.kind == "file" else None,
                    "error": self._errors.get(cam_id),
                }
            return {"mediamtx": {"ok": self.mediamtx_error is None, "error": self.mediamtx_error}, "cameras": cams}


    def _run(self) -> None:
        client = httpx.Client(timeout=2.0)
        while not self._stop.is_set():
            try:
                self._tick(client)
            except Exception:  # noqa: BLE001
                log.exception("ошибка в наблюдателе источников")
            self._stop.wait(1.0)
        client.close()

    def _tick(self, client: httpx.Client) -> None:
        with self._lock:
            desired = dict(self._desired)

        try:
            r = client.get(f"{self.api}/v3/paths/list", params={"itemsPerPage": 1000})
            r.raise_for_status()
            self._ready = {p["name"] for p in r.json().get("items", []) if p.get("ready")}
            if self.mediamtx_error:
                log.info("MediaMTX доступен")
            self.mediamtx_error = None
        except Exception as e:  # noqa: BLE001
            msg = f"MediaMTX недоступен на {self.api}: {e}"
            if self.mediamtx_error != msg:
                log.error(msg)
            self.mediamtx_error = msg
            self._ready = set()
            self._configured.clear()  # mediamtx перезапустился - пути заново

        for cam_id, proc in list(self._procs.items()):
            if desired.get(cam_id) != proc.desired:
                self._kill(cam_id)
        for cam_id, source in list(self._configured.items()):
            d = desired.get(cam_id)
            if d is None or d.kind != "rtsp" or d.source != source:
                self._delete_path(client, cam_id)

        now = time.monotonic()
        for cam_id, d in desired.items():
            if d.kind == "file":
                proc = self._procs.get(cam_id)
                if proc and proc.popen.poll() is None:
                    if now - proc.started > 15:
                        self._fails[cam_id] = 0
                    continue
                if proc:
                    self._fail(cam_id, f"ffmpeg завершился с кодом {proc.popen.returncode}, "
                                       f"см. {self.logs_dir / f'ffmpeg-{path_name(cam_id)}.log'}")
                    self._kill(cam_id)
                if self.mediamtx_error is None and now >= self._next_try.get(cam_id, 0):
                    self._start_ffmpeg(d)
            elif d.kind == "rtsp" and cam_id not in self._configured:
                if self.mediamtx_error is None and now >= self._next_try.get(cam_id, 0):
                    self._add_path(client, d)

        for cam_id in list(self._errors):
            if cam_id not in desired:
                self._errors.pop(cam_id, None)

    def _fail(self, cam_id: int, msg: str) -> None:
        if self._errors.get(cam_id) != msg:
            log.error("камера %s: %s", cam_id, msg)
        self._errors[cam_id] = msg
        n = self._fails.get(cam_id, 0)
        self._fails[cam_id] = n + 1
        self._next_try[cam_id] = time.monotonic() + RESTART_PAUSES[min(n, len(RESTART_PAUSES) - 1)]


    def ffmpeg_command(self, d: Desired, path: Path, offset: float | None = None) -> list[str]:
        scale = ["-vf", f"scale={self.width}:-2"] if self.width > 0 else []
        rate = ["-maxrate", self.maxrate, "-bufsize", _double(self.maxrate)] if self.maxrate else []
        if self.codec == "vp8":
            codec_args = ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "5", "-g", "30"]
            frame_args = ["-pix_fmt", "yuv420p"]
        else:
            codec_args = ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency"]
            frame_args = ["-bf", "0", "-pix_fmt", "yuv420p"]
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
            *loop_input(path, offset), "-an", *codec_args, *scale, *frame_args, *rate,
            "-force_key_frames", "expr:gte(t,n_forced*1)",
            "-f", "rtsp", "-rtsp_transport", "tcp", self.rtsp_url(d.camera_id),
        ]

    def _start_ffmpeg(self, d: Desired) -> None:
        path = (self.videos_dir / d.source).resolve()
        if self.videos_dir.resolve() not in path.parents or not path.is_file():
            self._fail(d.camera_id, f"нет видеофайла {d.source} в {self.videos_dir}")
            return
        if not shutil.which("ffmpeg"):
            self._fail(d.camera_id, "ffmpeg не найден в PATH")
            return
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(self.logs_dir / f"ffmpeg-{path_name(d.camera_id)}.log", "ab")
        if path not in self._durations:
            self._durations[path] = probe_duration(path)
        dur = self._durations[path]
        offset = loop_offset(dur, time.time() - self.lag_s) if dur else None
        popen = subprocess.Popen(self.ffmpeg_command(d, path, offset), stdout=log_file, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL)
        self._procs[d.camera_id] = _Proc(d, popen, time.monotonic(), log_file)
        self._errors.pop(d.camera_id, None)
        log.info("камера %s: ffmpeg публикует %s в %s (pid %s)", d.camera_id, d.source, path_name(d.camera_id), popen.pid)

    def _kill(self, cam_id: int) -> None:
        proc = self._procs.pop(cam_id, None)
        if not proc:
            return
        if proc.popen.poll() is None:
            proc.popen.terminate()
            try:
                proc.popen.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.popen.kill()
        proc.log_file.close()


    # mediamtx control api
    def _add_path(self, client: httpx.Client, d: Desired) -> None:
        name = path_name(d.camera_id)
        body = {"source": d.source, "sourceOnDemand": False, "rtspTransport": "tcp"}
        try:
            r = client.post(f"{self.api}/v3/config/paths/add/{name}", json=body)
            if r.status_code == 400 and "already exists" in r.text:
                r = client.patch(f"{self.api}/v3/config/paths/patch/{name}", json=body)
            r.raise_for_status()
            self._configured[d.camera_id] = d.source
            self._errors.pop(d.camera_id, None)
            log.info("камера %s: MediaMTX тянет %s в %s", d.camera_id, d.source, name)
        except Exception as e:  # noqa: BLE001
            detail = getattr(getattr(e, "response", None), "text", "") or str(e)
            self._fail(d.camera_id, f"MediaMTX не принял источник {d.source}: {detail}")

    def _delete_path(self, client: httpx.Client, cam_id: int) -> None:
        try:
            client.delete(f"{self.api}/v3/config/paths/delete/{path_name(cam_id)}")
        except Exception as e:  # noqa: BLE001
            log.warning("камера %s: не удалось удалить путь в MediaMTX: %s", cam_id, e)
        self._configured.pop(cam_id, None)
