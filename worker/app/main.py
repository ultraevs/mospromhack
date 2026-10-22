from __future__ import annotations

import logging
import signal
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import numpy as np

from shared.config import AppConfig, Settings, get_settings, load_config
from shared.schemas import EventCreate, WorkerCamera, WorkerConfig
from worker.app.capture import CameraReader
from worker.app.client import BackendClient, DetectionsSender
from worker.app.detector import Detector, create_detector
from worker.app.model_spec import ModelSpec, load_all_specs
from worker.app.pipeline import CameraPipeline, check_classes
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
    pipeline: CameraPipeline = field(default_factory=CameraPipeline)
    last_seq: int = 0
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
        self._pending: deque[EventOpen | EventClose] = deque()  # то что не ушло из-за обрыва
        self._captured: dict[int, int] = {}
        self._infer_ms: dict[str, deque] = {name: deque(maxlen=300) for name in specs}
        self._batch: dict[str, int] = {}
        self._config_error: str | None = None
        self._running = True
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
                self.cameras.pop(cam_id).reader.stop()
        for cam_id, cam in wanted.items():
            if cam_id in self.cameras:
                self.cameras[cam_id].camera = cam  # зоны/сизы меняются на лету без переоткрытия потока
            else:
                self.cameras[cam_id] = CameraState(cam, self._make_reader(cam_id, cam.rtsp_url).start())


    def tick_ppe(self) -> int:
        spec = self.specs["ppe"]
        fresh = []
        for state in self.cameras.values():
            frame = state.reader.latest()
            if frame is not None and frame.seq != state.last_seq:
                state.last_seq = frame.seq
                fresh.append((state, frame))
        if not fresh:
            return 0

        prepared = [preprocess(frame.image, spec.input_size) for _, frame in fresh]
        batch = np.stack([chw for chw, _ in prepared])
        t0 = time.perf_counter()
        outputs = self.detector.infer("ppe", batch)
        self._infer_ms["ppe"].append((time.perf_counter() - t0) * 1000)
        self._batch["ppe"] = len(batch)

        for (state, frame), (_, info), output in zip(fresh, prepared, outputs):
            dets = decode(output, spec, info)
            self._handle(state, frame.ts_ms, frame.image.shape[1], frame.image.shape[0], dets, spec)
            state.processed.append(time.monotonic())
        return len(fresh)

    def _handle(self, state: CameraState, ts_ms: int, w: int, h: int, dets: Detections, spec: ModelSpec) -> None:
        assert self.remote is not None
        msg, actions = state.pipeline.process_ppe(dets, spec, w, h, ts_ms, state.camera, self.remote.rules)
        self.sender.send(msg.model_dump_json())
        for action in actions:
            if isinstance(action, EventOpen):
                self._captured[action.key] = ts_ms
            self._pending.append((state.camera.id, action))
        self.flush_events()

    def flush_events(self) -> None:
        while self._pending:
            camera_id, action = self._pending[0]
            try:
                if isinstance(action, EventOpen):
                    event = self.client.post_event(EventCreate(
                        type=action.type, severity=action.severity, camera_id=camera_id, zone_id=action.zone_id,
                        track_id=action.track_id, started_at=datetime.fromtimestamp(action.t, timezone.utc),
                        confidence=action.confidence, bbox=list(action.bbox) if action.bbox else None,
                        details=action.details, captured_ts=self._captured.pop(action.key, None)))
                    self._event_ids[action.key] = event.id
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
            "streams": len(self.cameras),
            "cameras": {str(cam_id): {"online": s.reader.online, "error": s.reader.error,
                                      "read_fps": s.reader.read_fps(), "proc_fps": s.proc_fps()}
                        for cam_id, s in self.cameras.items()},
        }


    def run(self) -> None:
        period = 1.0 / self.config.worker.ppe_fps
        next_ppe = next_config = next_stats = time.monotonic()
        while self._running:
            now = time.monotonic()
            if now >= next_config:
                self.sync_config()
                next_config = now + CONFIG_POLL_S
            if now >= next_ppe:
                if self.remote:
                    self.tick_ppe()
                next_ppe = max(next_ppe + period, time.monotonic())
            if now >= next_stats:
                try:
                    self.flush_events()
                    self.client.post_stats(self.stats())
                except Exception:  # noqa: BLE001
                    pass
                next_stats = now + STATS_PERIOD_S
            time.sleep(max(0.0, min(next_ppe, next_config, next_stats) - time.monotonic()))

    def stop(self) -> None:
        self._running = False

    def shutdown(self) -> None:
        for state in self.cameras.values():
            state.reader.stop()
        self.sender.stop()
        self.client.close()


def build(settings: Settings, config: AppConfig) -> Worker:
    specs = load_all_specs(settings.models_dir)
    if "ppe" not in specs:
        raise SystemExit(f"нет модели ppe в {settings.models_dir}; запусти scripts/sync_models.ps1")
    check_classes(specs["ppe"])
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
