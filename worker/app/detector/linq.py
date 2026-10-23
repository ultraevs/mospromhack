from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import yaml

from worker.app.detector.linq_raw import ImagePacker, load_raw_meta, pack, unpack
from worker.app.model_spec import ModelSpec
from worker.app.postprocess import LetterboxInfo, letterbox

log = logging.getLogger("worker.detector")

EXCHANGE = "tpu.direct"
INPUT_KEY = "input"


def load_linq_meta(spec: ModelSpec) -> tuple[Path, dict]:
    linq_dir = spec.onnx_path.parent / "linq"
    path = linq_dir / "meta.yaml"
    if not path.exists():
        raise FileNotFoundError(f"нет {path}; скомпилируй модель: scripts/linq_compile.py --model {spec.name}")
    return linq_dir, yaml.safe_load(path.read_text(encoding="utf-8"))


# голова режется как у yolo12m в sdk
def yolo_tail(parts: dict[str, np.ndarray], names: list[str], strides: np.ndarray) -> np.ndarray:
    xy, wh, cls = (np.asarray(parts[n], dtype=np.float32) for n in names)
    return np.concatenate([np.concatenate([xy, wh], axis=1) * strides, cls], axis=1)


def make_tail(linq_dir: Path, meta: dict):
    names = meta["tpu_outputs"]
    kind = meta.get("tail") or ("onnx" if meta.get("tail_onnx") else None)
    if kind is None:
        return lambda parts: np.asarray(parts[names[0]], dtype=np.float32)
    if kind == "yolo_detect":
        strides = np.load(linq_dir / meta["strides"])
        return lambda parts: yolo_tail(parts, names, strides)
    if kind == "onnx":
        import onnxruntime as ort
        session = ort.InferenceSession(str(linq_dir / meta["tail_onnx"]), providers=["CPUExecutionProvider"])
        return lambda parts: session.run(None, {n: np.asarray(parts[n], dtype=np.float32) for n in names})[0]
    raise ValueError(f"meta.yaml: неизвестный хвост {kind!r}")


class PytpuRunner:
    def __init__(self, programs: dict[str, Path], device_index: int = 0):
        try:
            import pytpu as tpu
        except ImportError as e:
            raise RuntimeError("нет pytpu: transport pytpu работает только на машине с LinQ TPU; "
                               "со стендом работай через transport: cloud") from e
        devices = tpu.Device.list_devices()
        if not devices:
            raise RuntimeError("устройство LinQ TPU не найдено (ls /dev/tpu*)")
        self._stack = ExitStack()
        self._inference = {}
        try:
            dev = self._stack.enter_context(tpu.Device.open(devices[device_index]))
            for name, path in programs.items():
                program = self._stack.enter_context(dev.load(str(path)))
                self._inference[name] = self._stack.enter_context(program.inference())
        except Exception:
            self._stack.close()
            raise
        self.where = str(devices[device_index])

    def run(self, model: str, inputs: list[dict[str, np.ndarray]]) -> list[tuple[dict[str, np.ndarray], float]]:
        out = []
        for x in inputs:
            t0 = time.perf_counter()
            parts = self._inference[model].sync(x)
            out.append((parts, (time.perf_counter() - t0) * 1000))
        return out

    def close(self) -> None:
        self._stack.close()


class CloudRunner:
    def __init__(self, programs: dict[str, str], broker: str, port: int = 5672, timeout_s: float = 20,
                 raw: bool = False):
        try:
            import pika
            import tpu_pycloud_format as fmt
        except ImportError as e:
            raise RuntimeError("для transport cloud нужны pika и tpu_pycloud_format "
                               "(pip install pika hack_materials/stand/tpu_pycloud_format-0.0.1-py3-none-any.whl)") from e
        self.pika, self.fmt = pika, fmt
        self.programs = programs
        self.raw = raw
        self.params = pika.ConnectionParameters(broker, port, credentials=pika.PlainCredentials("guest", "guest"),
                                                heartbeat=600, blocked_connection_timeout=timeout_s)
        self.timeout_s = timeout_s
        self.where = f"cloud {broker}:{port}"
        self._local = threading.local()
        self._all: list = []
        self._channel()

    def _channel(self):
        ch = getattr(self._local, "channel", None)
        if ch is not None and ch.is_open:
            return ch, self._local.queue
        conn = self.pika.BlockingConnection(self.params)
        ch = conn.channel()
        queue = f"worker_{uuid.uuid4().hex[:12]}"
        # exclusive вместо auto_delete: брокер удаляет auto_delete очередь после первого cancel
        ch.queue_declare(queue=queue, exclusive=True)
        ch.queue_bind(exchange=EXCHANGE, queue=queue, routing_key=queue)
        self._local.conn, self._local.channel, self._local.queue = conn, ch, queue
        self._all.append(conn)
        return ch, queue

    def run(self, model: str, inputs: list[dict[str, np.ndarray]]) -> list[tuple[dict[str, np.ndarray], float]]:
        fmt = self.fmt
        ch, queue = self._channel()
        ids = []
        for x in inputs:
            task = uuid.uuid4().hex
            ids.append(task)
            ch.basic_publish(exchange=EXCHANGE, routing_key=INPUT_KEY, body=fmt.serialize(fmt.PyTask(x)),
                             properties=self.pika.BasicProperties(headers={
                                 fmt.TARGET_URI_ATTR_NAME: f"file://{self.programs[model]}?raw={self.raw}",
                                 fmt.RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([queue]),
                                 fmt.MESSAGE_ID_ATTR_NAME: task,
                             }))
        got: dict[str, tuple[dict, float]] = {}
        deadline = time.monotonic() + self.timeout_s
        for method, props, body in ch.consume(queue, auto_ack=True, inactivity_timeout=0.5):
            if method is not None:
                task = (props.headers or {}).get(fmt.MESSAGE_ID_ATTR_NAME)
                data, _, worker = fmt.deserialize(body)
                if not data:
                    ch.cancel()
                    raise RuntimeError(f"облако linq: {worker['error_message'] or 'пустой ответ'} "
                                       f"(status {worker['status_code']})")
                if task in ids:
                    got[task] = (data, float(worker["inference_time"]) * 1000)
                if len(got) == len(ids):
                    break
            if time.monotonic() > deadline:
                ch.cancel()
                raise TimeoutError(f"облако linq не ответило за {self.timeout_s} с ({len(got)}/{len(ids)})")
        ch.cancel()
        return [got[t] for t in ids]

    def close(self) -> None:
        for conn in self._all:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


class LinqDetector:
    name = "linq"

    def __init__(self, specs: dict[str, ModelSpec], transport: str = "pytpu", broker: str = "10.0.114.30",
                 port: int = 5672, program_dir: str = "", timeout_s: float = 20, batch: int = 8, raw: bool = False):
        self.specs = specs
        self.warnings: list[str] = []
        self.meta: dict[str, dict] = {}
        self._tails = {}
        self.timings: dict[str, dict[str, float]] = {}
        self._batch = batch if transport == "cloud" else 1
        self.raw = raw and transport == "cloud"
        self._raw: dict[str, tuple[int, dict, dict]] = {}
        self._packers: dict[str, ImagePacker] = {}

        local = {}
        for name, spec in specs.items():
            linq_dir, meta = load_linq_meta(spec)
            self.meta[name] = meta
            self._tails[name] = make_tail(linq_dir, meta)
            self.timings[name] = {"frames": 0, "tpu_ms": 0.0, "roundtrip_ms": 0.0, "tail_ms": 0.0, "pack_ms": 0.0}
            local[name] = linq_dir / meta["program"]
            if self.raw:
                # раскладку берём из самой программы, она должна лежать рядом и локально
                hw, ins, outs = load_raw_meta(local[name])
                self._raw[name] = (hw["ddr_word_len"], ins, outs)
                self._packers[name] = ImagePacker(ins[meta["input"]], hw["ddr_word_len"])

        if transport == "cloud":
            if not program_dir or "{login}" in program_dir:
                raise RuntimeError("для transport cloud задай LINQ_LOGIN в .env (папка программ /auto/cache/<логин>)")
            self.runner = CloudRunner({n: f"{program_dir.rstrip('/')}/{p.name}" for n, p in local.items()},
                                      broker, port, timeout_s, raw=self.raw)
        elif transport == "pytpu":
            for path in local.values():
                if not path.exists():
                    raise FileNotFoundError(f"нет программы {path}")
            self.runner = PytpuRunner(local)
        else:
            raise ValueError(f"неизвестный transport {transport!r}; допустимо: cloud, pytpu")
        self.device = f"linq ({self.runner.where}{', raw' if self.raw else ''})"
        log.info("LinQ: %s, модели %s", self.runner.where, {n: m["program"] for n, m in self.meta.items()})

    def batch_limit(self, model: str) -> int | None:
        return self._batch

    # воркер зовёт вместо preprocess, если метод есть: в raw кадр сразу пакуется из uint8
    def prepare(self, model: str, image_bgr: np.ndarray) -> tuple[np.ndarray, LetterboxInfo] | None:
        if not self.raw:
            return None
        boxed, info = letterbox(image_bgr, self.specs[model].input_size)
        return self._packers[model](boxed), info

    def infer(self, model: str, batch: np.ndarray) -> np.ndarray:
        meta, tail, stats = self.meta[model], self._tails[model], self.timings[model]
        t0 = time.perf_counter()
        if self.raw:
            dwl, ins, outs = self._raw[model]
            t_in = ins[meta["input"]]
            # uint8 — уже упаковано в prepare
            inputs = [{meta["input"]: x if x.dtype == np.uint8 else pack(x[None], t_in, dwl)} for x in batch]
        else:
            inputs = [{meta["input"]: x[None]} for x in np.ascontiguousarray(batch, dtype=np.float32)]
        t1 = time.perf_counter()
        results = self.runner.run(model, inputs)
        t2 = time.perf_counter()
        if self.raw:
            results = [({n: unpack(parts[n], outs[n]) for n in outs}, ms) for parts, ms in results]
        out = np.concatenate([tail(parts) for parts, _ in results])
        stats["frames"] += len(inputs)
        stats["tpu_ms"] += sum(ms for _, ms in results)
        stats["pack_ms"] += (t1 - t0) * 1000
        stats["roundtrip_ms"] += (t2 - t1) * 1000
        stats["tail_ms"] += (time.perf_counter() - t2) * 1000
        return out

    def close(self) -> None:
        self.runner.close()
