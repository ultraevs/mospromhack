from __future__ import annotations

import contextlib
import logging
import math
import queue
import signal
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import cv2
import numpy as np
import psutil

from shared.config import ROOT, AppConfig, Settings, get_settings, load_config
from shared.schemas import EventCreate, WorkerCamera, WorkerConfig, ZoneType
from worker.app.capture import CameraReader, open_capture
from worker.app.client import BackendClient, DetectionsSender
from worker.app.detector import Detector, create_detector
from worker.app.evidence import (JPEG_QUALITY_SNAPSHOT, CameraEvidence, ClipBuilder, annotate, encode_jpeg)
from worker.app.model_spec import ModelSpec, load_all_specs
from worker.app.crops import crop_box, merge, only_classes, shift
from worker.app.pipeline import CameraPipeline, check_classes, norm_box
from worker.app.rules.state import CIGARETTE, HEAD_CLASSES, PERSON
from worker.app.postprocess import Detections, decode, preprocess
from worker.app.rules.types import EventClose, EventOpen

log = logging.getLogger("worker")

CONFIG_POLL_S = 2.0
STATS_PERIOD_S = 1.0
MAX_PENDING = 200


HAZARD_GROUP = 4


@dataclass
class CameraState:
    camera: WorkerCamera
    reader: CameraReader
    evidence: CameraEvidence
    pipeline: CameraPipeline = field(default_factory=CameraPipeline)
    last_seq: int = 0
    next_due: float = 0.0
    hazard_due: float = 0.0
    processed: deque = field(default_factory=lambda: deque(maxlen=120))

    def proc_fps(self) -> float:
        now = time.monotonic()
        return round(sum(1 for t in self.processed if now - t <= 5.0) / 5.0, 1)


# python -m worker.app.main из корня
class Worker:
    def __init__(self, config: AppConfig, specs: dict[str, ModelSpec], detector: Detector, client: BackendClient,
                 sender: DetectionsSender, reader_factory: Callable[[int, str], CameraReader] | None = None):
        self.config = config
        self.specs = specs
        self.detector = detector
        self.client = client
        self.sender = sender
        self._make_reader = reader_factory or self._default_reader
        self.remote: WorkerConfig | None = None
        self.cameras: dict[int, CameraState] = {}
        self._event_ids: dict[int, str] = {}
        self._snapshots: dict[int, tuple[bytes, bytes]] = {}
        self._clip_ids: dict[int, str | None] = {}
        self._clips_done: queue.Queue = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="evidence")
        self._cpu = ThreadPoolExecutor(max_workers=4, thread_name_prefix="prep")
        self._clips = ClipBuilder(self._pool, lambda key, path: self._clips_done.put((key, path)))
        self.clips_built = 0
        self.clips_failed = 0
        self._pending: deque[EventOpen | EventClose] = deque()  # то что не ушло из-за обрыва
        self._captured: dict[int, int] = {}
        self._infer_ms: dict[str, deque] = {name: deque(maxlen=300) for name in specs}
        self._batch: dict[str, int] = {}
        self._crop_times: deque = deque(maxlen=2000)
        self._hazard_checks: deque = deque(maxlen=4000)
        self.paced = False
        self._config_error: str | None = None
        self._running = True
        self.period_s = 0.0
        self._busy: deque = deque(maxlen=600)  # доля занятости цикла, около 1 = не вывозим
        self.session = uuid.uuid4().hex
        self._ps = psutil.Process()
        self._spent: dict[str, float] = {}
        self._spent_since = time.monotonic()
        self._cpu_seen: tuple[float, float, dict[int, float]] | None = None

    @contextlib.contextmanager
    def _timed(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._spent[name] = self._spent.get(name, 0.0) + time.perf_counter() - t0

    def loop_breakdown(self) -> dict:
        now = time.monotonic()
        dt = max(1e-3, now - self._spent_since)
        out = {name: round(seconds / dt * 1000) for name, seconds in sorted(self._spent.items(), key=lambda kv: -kv[1])}
        self._spent, self._spent_since = {}, now
        return out

    def _default_reader(self, cam_id: int, url: str) -> CameraReader:
        d = self.config.worker.decode
        logs = ROOT / "logs"
        logs.mkdir(exist_ok=True)
        return CameraReader(cam_id, url, lambda u: open_capture(
            u, d.width, d.hwaccel, self.config.worker.ppe_fps, d.backend, logs / f"decode-cam-{cam_id}.log"))


    def sync_config(self) -> None:
        try:
            remote = self.client.get_config()
        except Exception as e:  # noqa: BLE001
            msg = f"backend недоступен: {e}"
            if msg != self._config_error:
                log.error(msg)
            self._config_error = msg
            return
        self._config_error = None
        if self.remote and remote.version == self.remote.version:
            return
        log.info("конфигурация версии %s: камер %s", remote.version, len(remote.cameras))
        self.remote = remote
        wanted = {c.id: c for c in remote.cameras}
        for cam_id in list(self.cameras):
            cam = wanted.get(cam_id)
            if cam is None or cam.rtsp_url != self.cameras[cam_id].reader.url:
                self.cameras.pop(cam_id).reader.stop(wait=False)
        for cam_id, cam in wanted.items():
            if cam_id in self.cameras:
                self.cameras[cam_id].camera = cam  # зоны/сизы меняются на лету без переоткрытия потока
            else:
                ev = self.config.evidence
                self.cameras[cam_id] = CameraState(cam, self._make_reader(cam_id, cam.rtsp_url).start(),
                                                   CameraEvidence(ev.pre_seconds, ev.post_seconds, ev.clip_width, self._pool))


    def tick_ppe(self) -> int:
        spec = self.specs["ppe"]
        with self._timed("клипы: сбор и отправка"):
            self._collect_clips()
        fresh = []
        now = time.monotonic()
        for state in self.cameras.values():
            if self.paced and hasattr(state.reader, "take_new"):
                for frame in state.reader.take_new():
                    state.last_seq = frame.seq
                    fresh.append((state, frame))
                continue
            frame = state.reader.latest()
            if frame is not None and frame.seq != state.last_seq and (self.paced or now >= state.next_due):
                state.last_seq = frame.seq
                state.next_due = max(state.next_due + self.period_s, now + self.period_s / 2)
                fresh.append((state, frame))
        if not fresh:
            return 0

        with self._timed("ppe: пред- и постобработка, инференс"):
            all_dets = self._infer_frames("ppe", [frame.image for _, frame in fresh])
        head = self.config.worker.crop_pass.head
        for (state, frame), dets in zip(fresh, all_dets):
            if head.enabled:
                with self._timed("ppe: второй проход по головам"):
                    dets = self._head_pass(frame.image, dets, spec, getattr(frame, "px_scale", 1.0))
            with self._timed("буфер кадров: постановка в очередь"):
                state.evidence.add_frame(frame.image, frame.ts_ms)
            self._handle(state, frame, dets, spec)
            state.processed.append(time.monotonic())
        return len(fresh)

    def _collect_clips(self) -> None:
        now_ms = int(time.time() * 1000)
        for state in self.cameras.values():
            for key, frames in state.evidence.ready_clips(now_ms):
                if len(frames) >= 2:
                    self._clips.submit(key, frames)
                else:
                    log.error("клип события %s не собран: в буфере %s кадров", self._clip_ids.get(key) or key, len(frames))
                    self._clip_ids.pop(key, None)
                    self.clips_failed += 1
        waiting = []
        while True:
            try:
                key, path = self._clips_done.get_nowait()
            except queue.Empty:
                break
            if path is None:
                self._clip_ids.pop(key, None)
                self.clips_failed += 1
                continue
            event_id = self._clip_ids.get(key)
            if event_id is None:
                if key in self._clip_ids:
                    waiting.append((key, path))
                continue
            try:
                self.client.upload_clip(event_id, path)
                self.clips_built += 1
                self._clip_ids.pop(key, None)
                log.info("событие %s: клип догружен", event_id)
            except Exception as e:  # noqa: BLE001
                log.error("событие %s: клип не догружен: %s", event_id, e)
                self.clips_failed += 1
                self._clip_ids.pop(key, None)
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        for item in waiting:
            self._clips_done.put(item)

    def _infer_frames(self, model: str, images: list[np.ndarray]) -> list[Detections]:
        spec = self.specs[model]
        size = spec.input_size
        step = self.detector.batch_limit(model) or len(images)
        chunks = [images[i:i + step] for i in range(0, len(images), step)]

        def finish(outputs: np.ndarray, infos: list) -> list[Detections]:
            return [decode(output, spec, info) for output, info in zip(outputs, infos)]

        if len(images) == 1:
            batch = np.empty((1, 3, size, size), dtype=np.float32)
            infos = [preprocess(images[0], size, out=batch[0])[1]]
            t0 = time.perf_counter()
            outputs = self.detector.infer(model, batch)
            self._infer_ms[model].append((time.perf_counter() - t0) * 1000)
            self._batch[model] = 1
            return finish(outputs, infos)

        prepared = []
        for chunk in chunks:
            batch = np.empty((len(chunk), 3, size, size), dtype=np.float32)
            prepared.append((batch, [self._cpu.submit(preprocess, image, size, batch[i]) for i, image in enumerate(chunk)]))
        finished, spent = [], 0.0
        for batch, futures in prepared:
            with self._timed(f"{model}: ожидание предобработки"):
                infos = [f.result()[1] for f in futures]
            t0 = time.perf_counter()
            with self._timed(f"{model}: инференс"):
                outputs = self.detector.infer(model, batch)
            spent += time.perf_counter() - t0
            finished.append(self._cpu.submit(finish, outputs, infos))
        self._infer_ms[model].append(spent * 1000)
        self._batch[model] = len(images)
        with self._timed(f"{model}: ожидание постобработки"):
            return [dets for future in finished for dets in future.result()]

    def _head_pass(self, image: np.ndarray, dets: Detections, spec: ModelSpec, px_scale: float = 1.0) -> Detections:
        cfg = self.config.worker.crop_pass.head
        h, w = image.shape[:2]
        person = spec.class_id(PERSON)
        small = [i for i in np.argsort(-dets.conf) if dets.cls[i] == person
                 and (dets.xyxy[i][3] - dets.xyxy[i][1]) * px_scale < cfg.max_person_height_px][:cfg.max_per_frame]
        boxes = [b for b in (crop_box(dets.xyxy[i], cfg.top_fraction, cfg.margin, w, h) for i in small) if b]
        if not boxes:
            return dets
        self._crop_times.extend([time.monotonic()] * len(boxes))
        head_ids = {spec.class_id(name) for name in HEAD_CLASSES}
        for (x1, y1, x2, y2), found in zip(boxes, self._infer_frames("ppe", [image[y1:y2, x1:x2] for x1, y1, x2, y2 in boxes])):
            dets = merge(dets, shift(only_classes(found, head_ids), x1, y1), spec.nms_iou)
        return dets

    def tick_hazard_due(self) -> int:
        if "hazard" not in self.specs or not self.cameras:
            return 0
        now = time.monotonic()
        period = 1.0 / self.config.worker.hazard_fps
        calls_per_period = max(1.0, period / (self.period_s or period))
        limit = math.ceil(math.ceil(len(self.cameras) / calls_per_period) / HAZARD_GROUP) * HAZARD_GROUP
        due = sorted((s for s in self.cameras.values() if now >= s.hazard_due), key=lambda s: s.hazard_due)[:limit]
        for state in due:
            state.hazard_due = max(state.hazard_due + period, now + period / 2) if state.hazard_due else now + period
        if not due:
            return 0
        with self._timed("hazard: весь проход"):
            return self.tick_hazard({s.camera.id for s in due})

    def tick_hazard(self, only: set[int] | None = None) -> int:
        if "hazard" not in self.specs or self.remote is None:
            return 0
        spec = self.specs["hazard"]
        current = [(state, frame) for state in self.cameras.values() if (frame := state.reader.latest()) is not None
                   and time.time() * 1000 - frame.ts_ms < 2000 and (only is None or state.camera.id in only)]
        if not current:
            return 0

        for (state, frame), dets in zip(current, self._infer_frames("hazard", [f.image for _, f in current])):
            h, w = frame.image.shape[:2]
            self._queue(state, frame, state.pipeline.process_hazard(dets, spec, w, h, frame.ts_ms, self.remote.rules))

        cfg = self.config.worker.crop_pass.cigarette
        if cfg.enabled:
            jobs = []
            smoking = self.remote.rules.smoking
            for state, frame in current:
                h, w = frame.image.shape[:2]
                zones = None if smoking.everywhere else frozenset(
                    z.id for z in state.camera.zones if z.type == ZoneType.flammable)
                for track_id, box in state.pipeline.select_crops(frame.ts_ms / 1000, w, h, cfg.max_per_tick,
                                                                 cfg.top_fraction, cfg.margin, zones):
                    jobs.append((state.pipeline, track_id, frame, box))
            if jobs:
                self._crop_times.extend([time.monotonic()] * len(jobs))
                cigarette = spec.class_id(CIGARETTE)
                images = [frame.image[y1:y2, x1:x2] for _, _, frame, (x1, y1, x2, y2) in jobs]
                for (pipeline, track_id, frame, (x1, y1, _, _)), found in zip(jobs, self._infer_frames("hazard", images)):
                    found = shift(only_classes(found, {cigarette}), x1, y1)
                    h, w = frame.image.shape[:2]
                    pipeline.apply_cigarette(track_id, frame.ts_ms / 1000,
                                             [(float(c), norm_box(b, w, h)) for c, b in zip(found.conf, found.xyxy)],
                                             smoking.upper_fraction)
        self._hazard_checks.extend([time.monotonic()] * len(current))
        self.flush_events()
        return len(current)

    def _queue(self, state: CameraState, frame, actions) -> None:
        for action in actions:
            if isinstance(action, EventOpen):
                self._captured[action.key] = frame.ts_ms
                try:
                    zone = next((z for z in state.camera.zones if z.id == action.zone_id), None)
                    marked = annotate(frame.image, action.bbox, action.type.value, zone.polygon if zone else None)
                    self._snapshots[action.key] = (encode_jpeg(marked, JPEG_QUALITY_SNAPSHOT),
                                                   encode_jpeg(frame.image, JPEG_QUALITY_SNAPSHOT))
                except Exception:  # noqa: BLE001
                    log.exception("не удалось подготовить стоп-кадры события")
                self._clip_ids[action.key] = None
                state.evidence.start_clip(action.key, frame.ts_ms)
            self._pending.append((state.camera.id, action))

    def _handle(self, state: CameraState, frame, dets: Detections, spec: ModelSpec) -> None:
        assert self.remote is not None
        h, w = frame.image.shape[:2]
        with self._timed("трекер, привязка, правила"):
            msg, actions = state.pipeline.process_ppe(dets, spec, w, h, frame.ts_ms, state.camera, self.remote.rules,
                                                      getattr(frame, "px_scale", 1.0))
        with self._timed("рамки: JSON и отправка"):
            self.sender.send(msg.model_dump_json())
        with self._timed("события: стоп-кадры"):
            self._queue(state, frame, actions)
        with self._timed("события: отправка в backend"):
            self.flush_events()

    def flush_events(self) -> None:
        while self._pending:
            camera_id, action = self._pending[0]
            try:
                if isinstance(action, EventOpen):
                    snapshot, clean = self._snapshots.get(action.key, (None, None))
                    event = self.client.post_event(EventCreate(
                        type=action.type, severity=action.severity, camera_id=camera_id, zone_id=action.zone_id,
                        track_id=action.track_id, started_at=datetime.fromtimestamp(action.t, timezone.utc),
                        confidence=action.confidence, bbox=list(action.bbox) if action.bbox else None,
                        details=action.details, captured_ts=self._captured.get(action.key)), snapshot, clean)
                    self._captured.pop(action.key, None)
                    self._snapshots.pop(action.key, None)
                    self._event_ids[action.key] = event.id
                    if action.key in self._clip_ids:
                        self._clip_ids[action.key] = event.id
                    log.info("событие %s открыто: %s, камера %s", event.id, action.type.value, camera_id)
                else:
                    event_id = self._event_ids.pop(action.key, None)
                    if event_id:
                        self.client.close_event(event_id, datetime.fromtimestamp(action.t, timezone.utc))
                        log.info("событие %s закрыто", event_id)
            except Exception as e:  # noqa: BLE001
                log.error("не удалось отправить событие в backend: %s; в очереди %s", e, len(self._pending))
                while len(self._pending) > MAX_PENDING:
                    self._pending.popleft()
                return
            self._pending.popleft()


    def cpu_usage(self) -> dict:
        now = time.monotonic()
        own = sum(self._ps.cpu_times()[:2])
        children: dict[int, float] = {}
        for child in self._ps.children(recursive=True):
            try:
                if child.name().lower().startswith("ffmpeg"):
                    children[child.pid] = sum(child.cpu_times()[:2])
            except psutil.Error:
                pass
        seen, self._cpu_seen = self._cpu_seen, (now, own, children)
        if seen is None or now - seen[0] < 0.2:
            return {"worker": 0.0, "decoders": 0.0}
        dt = now - seen[0]
        decoders = sum(t - seen[2][pid] for pid, t in children.items() if pid in seen[2])
        return {"worker": round((own - seen[1]) / dt, 2), "decoders": round(max(0.0, decoders) / dt, 2)}

    def stats(self) -> dict:
        infer = {}
        for name, values in self._infer_ms.items():
            if values:
                arr = np.array(values)
                infer[name] = {"avg_ms": round(float(arr.mean()), 1), "p95_ms": round(float(np.percentile(arr, 95)), 1),
                               "batch": self._batch.get(name, 0)}
        now = time.monotonic()
        online_fps = [s.proc_fps() for s in self.cameras.values() if s.reader.online]
        warnings = list(getattr(self.detector, "warnings", []))
        if not self.sender.connected:
            warnings.append("нет соединения для отправки рамок в backend")
        return {
            "session": self.session,
            "detector": self.detector.name,
            "device": self.detector.device,
            "precision": getattr(self.detector, "precision", {}),
            "warnings": warnings,
            "target_fps": {"ppe": self.config.worker.ppe_fps, "hazard": self.config.worker.hazard_fps},
            "inference": infer,
            "actual_fps": {"ppe": round(float(np.mean(online_fps)), 1) if online_fps else 0.0,
                           "hazard": round(sum(1 for t in self._hazard_checks if now - t <= 5.0) / 5.0
                                           / max(1, len(online_fps)), 1)},
            "cpu_cores": self.cpu_usage(),
            "cpu_machine": round(psutil.cpu_percent(interval=None) / 100 * psutil.cpu_count(), 1),
            "loop_ms_per_s": self.loop_breakdown(),
            "decode": {"backend": self.config.worker.decode.backend, "width": self.config.worker.decode.width,
                       "gpu": sum(1 for s in self.cameras.values() if getattr(s.reader, "decoder", "") == "gpu"),
                       "cpu": sum(1 for s in self.cameras.values() if getattr(s.reader, "decoder", "") == "cpu")},
            "crops_per_s": round(sum(1 for t in self._crop_times if now - t <= 5.0) / 5.0, 1),
            "crop_pass": {"cigarette": self.config.worker.crop_pass.cigarette.enabled,
                          "head": self.config.worker.crop_pass.head.enabled},
            "loop_load": round(min(1.0, sum(b for t, b in self._busy if now - t <= 5.0) / 5.0), 2),
            "streams": len(self.cameras),
            "clips": {"built": self.clips_built, "failed": self.clips_failed,
                      "pending": sum(s.evidence.pending() for s in self.cameras.values())},
            "cameras": {str(cam_id): {"online": s.reader.online, "error": s.reader.error,
                                      "read_fps": s.reader.read_fps(), "proc_fps": s.proc_fps()}
                        for cam_id, s in self.cameras.items()},
        }


    def run(self) -> None:
        self.period_s = 1.0 / self.config.worker.ppe_fps
        self.paced = self.config.worker.decode.backend == "ffmpeg"
        # опрашиваем в 3 раза чаще чем fps, иначе кадры теряются (было 3.4 вместо 6)
        period = self.period_s if self.paced else self.period_s / 3
        next_ppe = next_config = next_stats = time.monotonic()
        while self._running:
            now = time.monotonic()
            if now >= next_config:
                self.sync_config()
                next_config = now + CONFIG_POLL_S
            if now >= next_ppe:
                if self.remote and self.tick_ppe():
                    self.tick_hazard_due()
                next_ppe = max(next_ppe + period, time.monotonic())
            if now >= next_stats:
                try:
                    self.flush_events()
                    self.client.post_stats(self.stats())
                except Exception:  # noqa: BLE001
                    pass
                next_stats = now + STATS_PERIOD_S
            done = time.monotonic()
            self._busy.append((done, done - now))
            time.sleep(max(0.0, min(next_ppe, next_config, next_stats) - done))

    def stop(self) -> None:
        self._running = False

    def shutdown(self) -> None:
        for state in self.cameras.values():
            state.reader.stop()
        self.sender.stop()
        self.client.close()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._cpu.shutdown(wait=False, cancel_futures=True)
        self._clips.cleanup()


def build(settings: Settings, config: AppConfig) -> Worker:
    specs = load_all_specs(settings.models_dir)
    if "ppe" not in specs:
        raise SystemExit(f"нет модели ppe в {settings.models_dir}; запусти scripts/sync_models.ps1")
    check_classes(specs["ppe"], specs.get("hazard"))
    detector = create_detector(config.worker.detector, specs, config.worker.fake_scenario, config.worker.precision)
    base = f"http://{settings.backend_host}:{settings.backend_port}"
    client = BackendClient(base, settings.internal_token)
    sender = DetectionsSender(base, settings.internal_token).start()
    return Worker(config, specs, detector, client, sender)


def main() -> None:
    cv2.setNumThreads(1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    config = load_config(settings.config_path)
    if settings.ppe_fps:
        config.worker.ppe_fps = settings.ppe_fps
        log.info("частота обработки из переменной PPE_FPS: %s кадров/с", settings.ppe_fps)
    worker = build(settings, config)
    signal.signal(signal.SIGINT, lambda *_: worker.stop())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    log.info("воркер запущен: детектор %s, устройство %s", worker.detector.name, worker.detector.device)
    try:
        worker.run()
    finally:
        worker.shutdown()


if __name__ == "__main__":
    main()
