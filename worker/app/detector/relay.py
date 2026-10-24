from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid

import cv2
import numpy as np

from worker.app.detector.linq import load_linq_meta
from worker.app.model_spec import ModelSpec
from worker.app.postprocess import LetterboxInfo, letterbox

log = logging.getLogger("worker.detector")

JPEG_QUALITY = 85


# имя очереди посредника: от логина, но сам логин в брокер/логи не светим
def relay_queue(login: str) -> str:
    return "relay_" + hashlib.sha1(login.encode()).hexdigest()[:12]


# запрос: тело = склеенные jpeg, длины в заголовке
def pack_request(model: str, images: list[np.ndarray], quality: int = JPEG_QUALITY) -> tuple[bytes, dict]:
    parts = []
    for im in images:
        ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise ValueError("jpeg не закодировался")
        parts.append(buf.tobytes())
    return b"".join(parts), {"model": model, "sizes": json.dumps([len(p) for p in parts])}


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def unpack_request(body: bytes, headers: dict) -> tuple[str, list[np.ndarray]]:
    images, pos = [], 0
    for n in json.loads(_s(headers["sizes"])):
        images.append(cv2.imdecode(np.frombuffer(body[pos:pos + n], np.uint8), cv2.IMREAD_COLOR))
        pos += n
    return _s(headers["model"]), images


# ответ: на каждую картинку [[x1,y1,x2,y2,conf,cls],...] в координатах входа модели
def pack_reply(task: str, dets: list, timings: dict | None = None) -> bytes:
    return json.dumps({"id": task, "dets": dets, "t": timings or {}}, separators=(",", ":")).encode()


# детекции -> «выход модели» (4+nc, N), чтобы воркер прогнал обычный decode: пороги и nms те же
def dets_to_output(dets: list, nc: int) -> np.ndarray:
    out = np.zeros((4 + nc, len(dets)), np.float32)
    for j, (x1, y1, x2, y2, conf, cls) in enumerate(dets):
        out[:4, j] = ((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1)
        out[4 + int(cls), j] = conf
    return out


class RelayDetector:
    name = "relay"
    raw = True  # воркер зовёт prepare (letterbox в uint8), а не preprocess во float

    def __init__(self, specs: dict[str, ModelSpec], broker: str = "10.0.114.30", port: int = 5672,
                 timeout_s: float = 5, batch: int = 8, login: str = "", queue: str = "",
                 quality: int | None = None,
                 **_):
        import pika
        self.pika = pika
        self.specs = specs
        self.warnings: list[str] = []
        self.quality = quality or int(env("RELAY_JPEG_QUALITY", str(JPEG_QUALITY)))
        self.timeout_s = timeout_s
        self._batch = batch
        if not queue:
            if not login:
                raise RuntimeError("для detector: relay задай LINQ_LOGIN в .env (по нему имя очереди посредника)")
            queue = relay_queue(login)
        self.queue = queue
        # пороги те же, что у linq: meta.yaml лежит локально
        self.thresholds: dict[str, np.ndarray] = {}
        for name, spec in specs.items():
            try:
                _, meta = load_linq_meta(spec)
            except FileNotFoundError:
                continue
            if meta.get("thresholds"):
                self.thresholds[name] = np.array([meta["thresholds"].get(c, float(spec.thresholds[i]))
                                                  for i, c in enumerate(spec.classes)], dtype=np.float32)
        self.params = pika.ConnectionParameters(broker, port, credentials=pika.PlainCredentials("guest", "guest"),
                                                heartbeat=600, blocked_connection_timeout=timeout_s)
        self._local = threading.local()
        self._all: list = []
        self.timings: dict[str, dict[str, float]] = {n: {"frames": 0, "lost": 0, "roundtrip_ms": 0.0, "bytes_out": 0, "server_ms": 0.0,
                                                         "bytes_in": 0} for n in specs}
        self._last_err = 0.0
        self.device = f"linq через посредника ({broker}:{port})"

    def batch_limit(self, model: str) -> int | None:
        return self._batch

    def prepare(self, model: str, image_bgr: np.ndarray) -> tuple[np.ndarray, LetterboxInfo]:
        return letterbox(image_bgr, self.specs[model].input_size)

    def _channel(self):
        ch = getattr(self._local, "channel", None)
        if ch is not None and ch.is_open:
            return ch, self._local.reply
        conn = self.pika.BlockingConnection(self.params)
        ch = conn.channel()
        reply = ch.queue_declare(queue="", exclusive=True).method.queue
        self._local.conn, self._local.channel, self._local.reply = conn, ch, reply
        self._all.append(conn)
        return ch, reply

    def _drop_channel(self) -> None:
        conn = getattr(self._local, "conn", None)
        self._local.channel = None
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _call(self, model: str, images: list[np.ndarray]) -> list | None:
        body, headers = pack_request(model, images, self.quality)
        task = uuid.uuid4().hex
        ch, reply = self._channel()
        ch.basic_publish(exchange="", routing_key=self.queue, body=body,
                         properties=self.pika.BasicProperties(reply_to=reply, correlation_id=task, headers=headers,
                                                              expiration=str(int(self.timeout_s * 1000))))
        self.timings[model]["bytes_out"] += len(body)
        deadline = time.monotonic() + self.timeout_s
        try:
            for method, props, data in ch.consume(reply, auto_ack=True, inactivity_timeout=0.2):
                if method is not None and props.correlation_id == task:  # старые ответы после таймаута — мимо
                    self.timings[model]["bytes_in"] += len(data)
                    msg = json.loads(data)
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
                    self.timings[model]["server_ms"] += float(msg.get("t", {}).get("ms", 0))
                    return msg["dets"]
                if time.monotonic() > deadline:
                    return None
        finally:
            ch.cancel()

    def infer(self, model: str, batch: np.ndarray) -> list[np.ndarray]:
        stats, nc = self.timings[model], self.specs[model].nc
        t0 = time.perf_counter()
        try:
            dets = self._call(model, list(batch))
            err = None if dets is not None else f"посредник не ответил за {self.timeout_s} с"
        except Exception as e:  # noqa: BLE001
            self._drop_channel()
            dets, err = None, f"посредник: {e}"
        stats["frames"] += len(batch)
        stats["roundtrip_ms"] += (time.perf_counter() - t0) * 1000
        if dets is None:
            # воркер не падает: пустые детекции, лог не чаще раза в 10 с
            stats["lost"] += len(batch)
            if time.monotonic() - self._last_err > 10:
                self._last_err = time.monotonic()
                log.error("%s (%s), кадры без детекций", err, model)
            return [dets_to_output([], nc) for _ in batch]
        return [dets_to_output(d, nc) for d in dets]

    def close(self) -> None:
        for conn in self._all:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# pydantic кладёт в Settings только известные поля, RELAY_* читаем сами: окружение, потом .env
def env(name: str, default: str = "") -> str:
    if os.environ.get(name):
        return os.environ[name]
    from shared.config import ROOT
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    return default


def login_from_env() -> str:
    login = os.environ.get("LINQ_LOGIN", "")
    if not login:
        from shared.config import get_settings
        login = get_settings().linq_login
    if not login:
        # как linq_stand.py: файл с логинами стенда (в контейнере linq-vpn — /creds)
        from pathlib import Path
        from shared.config import ROOT
        for path in (Path("/creds"), ROOT / "hack_materials" / ".env-creds"):
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("jupyter_notebook_log="):
                        return line.split("=", 1)[1].strip()
    return login
