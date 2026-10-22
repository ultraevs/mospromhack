from __future__ import annotations

import logging
import queue
import signal
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import numpy as np

from shared.config import AppConfig, Settings, get_settings, load_config
from shared.schemas import EventCreate, WorkerCamera, WorkerConfig
from worker.app.capture import CameraReader
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


@dataclass
class CameraState:
    camera: WorkerCamera
    reader: CameraReader
    evidence: CameraEvidence
    pipeline: CameraPipeline = field(default_factory=CameraPipeline)
    last_seq: int = 0
    next_due: float = 0.0
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
        self._make_reader = reader_factory or (lambda cam_id, url: CameraReader(cam_id, url))
        self.remote: WorkerConfig | None = None
        self.cameras: dict[int, CameraState] = {}
        self._event_ids: dict[int, str] = {}
        self._snapshots: dict[int, tuple[bytes, bytes]] = {}
        self._clip_ids: dict[int, str | None] = {}
        self._clips_done: queue.Queue = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="evidence")
        self._clips = ClipBuilder(self._pool, lambda key, path: self._clips_done.put((key, path)))
        self.clips_built = 0
        self.clips_failed = 0
        self._pending: deque[EventOpen | EventClose] = deque()  # то что не ушло из-за обрыва
        self._captured: dict[int, int] = {}
        self._infer_ms: dict[str, deque] = {name: deque(maxlen=300) for name in specs}
        self._batch: dict[str, int] = {}
        self._crop_times: deque = deque(maxlen=2000)
        self._ticks: dict[str, deque] = {name: deque(maxlen=120) for name in specs}
        self._config_error: str | None = None
        self._running = True
        self.period_s = 0.0
        self._busy: deque = deque(maxlen=600)  # доля занятости цикла, около 1 = не вывозим
        self.session = uuid.uuid4().hex


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
        self._collect_clips()
        fresh = []
        now = time.monotonic()
        for state in self.cameras.values():
            frame = state.reader.latest()
            if frame is not None and frame.seq != state.last_seq and now >= state.next_due:
                state.last_seq = frame.seq
                state.next_due = max(state.next_due + self.period_s, now + self.period_s / 2)
                fresh.append((state, frame))
        if not fresh:
            return 0

        all_dets = self._infer_frames("ppe", [frame.image for _, frame in fresh])
        head = self.config.worker.crop_pass.head
        for (state, frame), dets in zip(fresh, all_dets):
            if head.enabled:
                dets = self._head_pass(frame.image, dets, spec)
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
        batch = np.empty((len(images), 3, spec.input_size, spec.input_size), dtype=np.float32)
        infos = [preprocess(image, spec.input_size, out=batch[i])[1] for i, image in enumerate(images)]
        t0 = time.perf_counter()
        outputs = self.detector.infer(model, batch)
        self._infer_ms[model].append((time.perf_counter() - t0) * 1000)
        self._batch[model] = len(batch)
        return [decode(output, spec, info) for output, info in zip(outputs, infos)]

    def _head_pass(self, image: np.ndarray, dets: Detections, spec: ModelSpec) -> Detections:
        cfg = self.config.worker.crop_pass.head
        h, w = image.shape[:2]
        person = spec.class_id(PERSON)
        small = [i for i in np.argsort(-dets.conf) if dets.cls[i] == person
                 and dets.xyxy[i][3] - dets.xyxy[i][1] < cfg.max_person_height_px][:cfg.max_per_frame]
        boxes = [b for b in (crop_box(dets.xyxy[i], cfg.top_fraction, cfg.margin, w, h) for i in small) if b]
        if not boxes:
            return dets
        self._crop_times.extend([time.monotonic()] * len(boxes))
        head_ids = {spec.class_id(name) for name in HEAD_CLASSES}
        for (x1, y1, x2, y2), found in zip(boxes, self._infer_frames("ppe", [image[y1:y2, x1:x2] for x1, y1, x2, y2 in boxes])):
            dets = merge(dets, shift(only_classes(found, head_ids), x1, y1), spec.nms_iou)
        return dets

    def tick_hazard(self) -> int:
        if "hazard" not in self.specs or self.remote is None:
            return 0
        spec = self.specs["hazard"]
        current = [(state, frame) for state in self.cameras.values() if (frame := state.reader.latest()) is not None
                   and time.time() * 1000 - frame.ts_ms < 2000]
        if not current:
            return 0

        for (state, frame), dets in zip(current, self._infer_frames("hazard", [f.image for _, f in current])):
            h, w = frame.image.shape[:2]
            self._queue(state, frame, state.pipeline.process_hazard(dets, spec, w, h, frame.ts_ms, self.remote.rules))

        cfg = self.config.worker.crop_pass.cigarette
        if cfg.enabled:
            jobs = []
            for state, frame in current:
                h, w = frame.image.shape[:2]
                for track_id, box in state.pipeline.select_crops(frame.ts_ms / 1000, w, h, cfg.max_per_tick,
                                                                 cfg.top_fraction, cfg.margin):
                    jobs.append((state.pipeline, track_id, frame, box))
            if jobs:
                self._crop_times.extend([time.monotonic()] * len(jobs))
                cigarette = spec.class_id(CIGARETTE)
                images = [frame.image[y1:y2, x1:x2] for _, _, frame, (x1, y1, x2, y2) in jobs]
                for (pipeline, track_id, frame, (x1, y1, _, _)), found in zip(jobs, self._infer_frames("hazard", images)):
                    found = shift(only_classes(found, {cigarette}), x1, y1)
                    h, w = frame.image.shape[:2]
                    if len(found):
                        best = int(np.argmax(found.conf))
                        pipeline.apply_cigarette(track_id, frame.ts_ms / 1000, float(found.conf[best]),
                                                 norm_box(found.xyxy[best], w, h))
                    else:
                        pipeline.apply_cigarette(track_id, frame.ts_ms / 1000, 0.0, None)
        self._ticks["hazard"].append(time.monotonic())
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
        msg, actions = state.pipeline.process_ppe(dets, spec, w, h, frame.ts_ms, state.camera, self.remote.rules)
        self.sender.send(msg.model_dump_json())
        self._queue(state, frame, actions)
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
            "warnings": warnings,
            "target_fps": {"ppe": self.config.worker.ppe_fps, "hazard": self.config.worker.hazard_fps},
            "inference": infer,
            "actual_fps": {"ppe": round(float(np.mean(online_fps)), 1) if online_fps else 0.0,
                           "hazard": round(sum(1 for t in self._ticks.get("hazard", ()) if now - t <= 5.0) / 5.0, 1)},
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
        # опрашиваем в 3 раза чаще чем fps, иначе кадры теряются (было 3.4 вместо 6)
        period = self.period_s / 3
        hazard_period = 1.0 / self.config.worker.hazard_fps
        next_ppe = next_hazard = next_config = next_stats = time.monotonic()
        while self._running:
            now = time.monotonic()
            if now >= next_config:
                self.sync_config()
                next_config = now + CONFIG_POLL_S
            if now >= next_ppe:
                if self.remote:
                    self.tick_ppe()
                next_ppe = max(next_ppe + period, time.monotonic())
            if now >= next_hazard:
                self.tick_hazard()
                next_hazard = max(next_hazard + hazard_period, time.monotonic())
            if now >= next_stats:
                try:
                    self.flush_events()
                    self.client.post_stats(self.stats())
                except Exception:  # noqa: BLE001
                    pass
                next_stats = now + STATS_PERIOD_S
            done = time.monotonic()
            self._busy.append((done, done - now))
            time.sleep(max(0.0, min(next_ppe, next_hazard, next_config, next_stats) - done))

    def stop(self) -> None:
        self._running = False

    def shutdown(self) -> None:
        for state in self.cameras.values():
            state.reader.stop()
        self.sender.stop()
        self.client.close()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._clips.cleanup()


def build(settings: Settings, config: AppConfig) -> Worker:
    specs = load_all_specs(settings.models_dir)
    if "ppe" not in specs:
        raise SystemExit(f"нет модели ppe в {settings.models_dir}; запусти scripts/sync_models.ps1")
    check_classes(specs["ppe"], specs.get("hazard"))
    detector = create_detector(config.worker.detector, specs, config.worker.fake_scenario)
    base = f"http://127.0.0.1:{settings.backend_port}"
    client = BackendClient(base, settings.internal_token)
    sender = DetectionsSender(base, settings.internal_token).start()
    return Worker(config, specs, detector, client, sender)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    worker = build(settings, load_config(settings.config_path))
    signal.signal(signal.SIGINT, lambda *_: worker.stop())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    log.info("воркер запущен: детектор %s, устройство %s", worker.detector.name, worker.detector.device)
    try:
        worker.run()
    finally:
        worker.shutdown()


if __name__ == "__main__":
    main()
