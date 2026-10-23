from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import threading
import time
import uuid
from collections import deque
from pathlib import PurePosixPath, PureWindowsPath

log = logging.getLogger("bridge")

QUEUE_ARGS = {"x-expires": 600000, "x-message-ttl": 30000}
DROP_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "upgrade",
                "content-encoding", "accept-encoding"}


def _clean(headers) -> list[list[str]]:
    items = headers.items() if hasattr(headers, "items") else headers
    return [[str(k), str(v)] for k, v in items if str(k).lower() not in DROP_HEADERS]


def encode_request(method: str, path: str, headers, body: bytes) -> tuple[dict, bytes]:
    return {"method": method.upper(), "path": path, "hdr": json.dumps(_clean(headers))}, bytes(body or b"")


def decode_request(amqp_headers: dict, body: bytes | None) -> tuple[str, str, list[list[str]], bytes]:
    h = amqp_headers or {}
    return _s(h.get("method", "GET")), _s(h.get("path", "/")), json.loads(_s(h.get("hdr", "[]"))), body or b""


def encode_response(status: int, headers, body: bytes) -> tuple[dict, bytes]:
    return {"status": int(status), "hdr": json.dumps(_clean(headers))}, bytes(body or b"")


def decode_response(amqp_headers: dict, body: bytes | None) -> tuple[int, list[list[str]], bytes]:
    h = amqp_headers or {}
    return int(h.get("status", 502)), json.loads(_s(h.get("hdr", "[]"))), body or b""


def _s(v) -> str:
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


def file_name(source: str) -> str:
    return PureWindowsPath(source).name if "\\" in source else PurePosixPath(source).name


def rewrite_config(cfg: dict, sources: dict[int, tuple[str, str]]) -> dict:
    for cam in cfg.get("cameras") or []:
        st = sources.get(int(cam.get("id", -1)))
        if st and st[0] == "file" and st[1]:
            cam["rtsp_url"] = f"syncfile://{file_name(st[1])}"
    return cfg


def sources_from_cameras(cams: list[dict]) -> dict[int, tuple[str, str]]:
    return {int(c["id"]): (str(c.get("source_type", "")), str(c.get("source", ""))) for c in cams if "id" in c}


def ws_url(backend: str) -> str:
    return backend.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/internal/detections"


def _params(broker: str):
    import pika
    p = pika.URLParameters(broker)
    p.heartbeat = 30
    p.blocked_connection_timeout = 30
    return p


def _declare(ch, prefix: str) -> None:
    for name in (f"{prefix}.req", f"{prefix}.ws"):
        ch.queue_declare(name, durable=False, arguments=QUEUE_ARGS)


class StandAmqp:
    # pika не потокобезопасна, весь amqp в одном потоке
    def __init__(self, broker: str, prefix: str, ws_backlog: int):
        self.broker, self.prefix = broker, prefix
        self.connected = False
        self._out: queue.Queue = queue.Queue()
        self._ws: deque[str] = deque(maxlen=ws_backlog)
        self._ws_lock = threading.Lock()
        self._waiters: dict[str, tuple] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="amqp", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)

    def send_ws(self, text: str) -> None:
        if not self.connected:
            return
        with self._ws_lock:
            self._ws.append(text)

    def request(self, loop, headers: dict, body: bytes):
        fut = loop.create_future()
        cid = uuid.uuid4().hex
        self._waiters[cid] = (loop, fut)
        self._out.put((cid, headers, body))
        return cid, fut

    def forget(self, cid: str) -> None:
        self._waiters.pop(cid, None)

    def _on_reply(self, ch, method, props, body):
        w = self._waiters.pop(props.correlation_id or "", None)
        if not w:
            return
        loop, fut = w
        resp = decode_response(props.headers or {}, body)
        loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(resp))

    def _run(self):
        import pika
        warned = False
        while not self._stop.is_set():
            conn = None
            try:
                conn = pika.BlockingConnection(_params(self.broker))
                ch = conn.channel()
                _declare(ch, self.prefix)
                reply_q = ch.queue_declare("", exclusive=True, auto_delete=True).method.queue
                ch.basic_consume(reply_q, self._on_reply, auto_ack=True)
                self.connected, warned = True, False
                log.info("amqp подключён %s, reply=%s", self.broker, reply_q)
                while not self._stop.is_set():
                    while True:
                        try:
                            cid, h, b = self._out.get_nowait()
                        except queue.Empty:
                            break
                        if cid not in self._waiters:
                            continue
                        ch.basic_publish("", f"{self.prefix}.req", b, pika.BasicProperties(
                            correlation_id=cid, reply_to=reply_q, headers=h, expiration="30000"))
                    with self._ws_lock:
                        frames, self._ws = list(self._ws), deque(maxlen=self._ws.maxlen)
                    for t in frames:
                        ch.basic_publish("", f"{self.prefix}.ws", t.encode(), pika.BasicProperties(expiration="5000"))
                    conn.process_data_events(time_limit=0.05)
            except Exception as e:  # noqa: BLE001
                if not warned:
                    log.info("amqp ошибка: %s; переподключаюсь", e)
                    warned = True
            finally:
                self.connected = False
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                for cid, (loop, fut) in list(self._waiters.items()):
                    self._waiters.pop(cid, None)
                    loop.call_soon_threadsafe(lambda f=fut: f.done() or f.set_exception(ConnectionError("amqp down")))
            self._stop.wait(2.0)


def run_stand(args) -> None:
    import asyncio
    from aiohttp import WSMsgType, web

    amqp = StandAmqp(args.broker, args.prefix, args.ws_backlog).start()

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        log.info("воркер подключил ws")
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                amqp.send_ws(msg.data)
            elif msg.type == WSMsgType.ERROR:
                break
        log.info("ws воркера закрыт")
        return ws

    async def handler(request):
        if request.path == "/internal/detections" and request.headers.get("Upgrade", "").lower() == "websocket":
            return await ws_handler(request)
        if not amqp.connected:
            return web.Response(status=503, text="bridge: amqp not connected")
        body = await request.read()
        h, b = encode_request(request.method, request.path_qs, request.headers.items(), body)
        t0 = time.monotonic()
        cid, fut = amqp.request(asyncio.get_running_loop(), h, b)
        try:
            status, hdrs, rbody = await asyncio.wait_for(fut, args.timeout)
        except asyncio.TimeoutError:
            amqp.forget(cid)
            log.info("%s %s таймаут", request.method, request.path_qs)
            return web.Response(status=504, text="bridge: timeout")
        except ConnectionError:
            return web.Response(status=503, text="bridge: amqp dropped")
        log.debug("%s %s -> %d %dB/%dB %.2fs", request.method, request.path_qs, status, len(b), len(rbody),
                  time.monotonic() - t0)
        resp = web.Response(status=status, body=rbody)
        for k, v in hdrs:
            resp.headers.add(k, v)
        return resp

    app = web.Application(client_max_size=512 * 1024 * 1024)
    app.router.add_route("*", "/{tail:.*}", handler)
    log.info("stand слушает 127.0.0.1:%d, prefix=%s", args.port, args.prefix)
    try:
        web.run_app(app, host="127.0.0.1", port=args.port, print=None, handle_signals=True)
    finally:
        amqp.stop()


class CameraSources:
    def __init__(self, http, ttl: float = 10.0):
        self._http, self._ttl = http, ttl
        self._at, self._data = 0.0, {}
        self._lock = threading.Lock()

    def get(self) -> dict:
        with self._lock:
            if time.monotonic() - self._at > self._ttl:
                try:
                    r = self._http.get("/api/cameras", timeout=5.0)
                    r.raise_for_status()
                    self._data, self._at = sources_from_cameras(r.json()), time.monotonic()
                except Exception as e:  # noqa: BLE001
                    log.info("не смог взять /api/cameras: %s", e)
            return self._data


class WsForwarder:
    def __init__(self, url: str, token: str, max_queue: int = 64):
        self.url, self.token = url, token
        self._q: queue.Queue[str] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ws-fwd", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)

    def put(self, text: str) -> None:
        try:
            self._q.put_nowait(text)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(text)
            except queue.Full:
                pass

    def _run(self):
        from websockets.sync.client import connect
        warned = False
        while not self._stop.is_set():
            try:
                with connect(self.url, additional_headers={"X-Internal-Token": self.token}, open_timeout=3) as ws:
                    log.info("ws к бэку подключён %s", self.url)
                    warned = False
                    while not self._stop.is_set():
                        try:
                            t = self._q.get(timeout=0.5)
                        except queue.Empty:
                            continue
                        ws.send(t)
            except Exception as e:  # noqa: BLE001
                if not warned:
                    log.info("ws к бэку упал: %s; повторяю", e)
                    warned = True
            self._stop.wait(1.0)


def _env_token() -> str:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from shared.config import get_settings
        return get_settings().internal_token
    except Exception:  # noqa: BLE001
        return ""


def run_local(args) -> None:
    from concurrent.futures import ThreadPoolExecutor

    import httpx
    import pika

    token = args.token or os.environ.get("INTERNAL_TOKEN", "") or _env_token()
    http = httpx.Client(base_url=args.backend.rstrip("/"), timeout=60.0)
    cams = CameraSources(http)
    fwd = WsForwarder(ws_url(args.backend), token).start()
    pool = ThreadPoolExecutor(max_workers=4)

    def do_request(h, body) -> tuple[dict, bytes]:
        method, path, hdrs, body = decode_request(h, body)
        # токен живет только у нас, со стенда его не шлем
        hdrs = [kv for kv in hdrs if kv[0].lower() != "x-internal-token"] + [["X-Internal-Token", token]]
        t0 = time.monotonic()
        try:
            r = http.request(method, path, headers=hdrs, content=body)
            status, rh, rb = r.status_code, list(r.headers.items()), r.content
        except Exception as e:  # noqa: BLE001
            log.info("%s %s ошибка бэка: %s", method, path, e)
            return encode_response(502, [["content-type", "text/plain"]], f"bridge: backend error {e}".encode())
        if (not args.no_rewrite and method == "GET" and path.split("?")[0] == "/internal/config"
                and status == 200):
            try:
                rb = json.dumps(rewrite_config(json.loads(rb), cams.get())).encode()
            except Exception as e:  # noqa: BLE001
                log.info("rewrite конфига не вышел: %s", e)
        log.debug("%s %s -> %d %dB/%dB %.2fs", method, path, status, len(body), len(rb), time.monotonic() - t0)
        return encode_response(status, rh, rb)

    stop = threading.Event()

    def amqp_loop():
        warned = False
        while not stop.is_set():
            conn = None
            try:
                conn = pika.BlockingConnection(_params(args.broker))
                ch = conn.channel()
                _declare(ch, args.prefix)
                ch.basic_qos(prefetch_count=8)

                def reply(props, fut):
                    try:
                        h, b = fut.result()
                    except Exception as e:  # noqa: BLE001
                        h, b = encode_response(500, [], f"bridge: {e}".encode())
                    if props.reply_to:
                        ch.basic_publish("", props.reply_to, b, pika.BasicProperties(
                            correlation_id=props.correlation_id, headers=h, expiration="30000"))

                def on_req(_ch, method, props, body):
                    fut = pool.submit(do_request, props.headers or {}, body)
                    fut.add_done_callback(lambda f: conn.add_callback_threadsafe(lambda: reply(props, f)))

                ch.basic_consume(f"{args.prefix}.req", on_req, auto_ack=True)
                ch.basic_consume(f"{args.prefix}.ws", lambda _c, _m, _p, b: fwd.put(b.decode(errors="replace")),
                                 auto_ack=True)
                log.info("amqp подключён %s, prefix=%s, бэк %s", args.broker, args.prefix, args.backend)
                warned = False
                while not stop.is_set():
                    conn.process_data_events(time_limit=0.5)
            except Exception as e:  # noqa: BLE001
                if not warned:
                    log.info("amqp ошибка: %s; переподключаюсь", e)
                    warned = True
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
            stop.wait(2.0)

    t = threading.Thread(target=amqp_loop, name="amqp", daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(0.5)
    except KeyboardInterrupt:
        log.info("выходим")
    finally:
        stop.set()
        t.join(timeout=3)
        fwd.stop()
        pool.shutdown(wait=False, cancel_futures=True)
        http.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="мост воркер<->бэк через rabbitmq")
    ap.add_argument("mode", choices=["stand", "local"])
    ap.add_argument("--prefix", required=True, help="общий на обеих сторонах, например bridge.team46")
    ap.add_argument("--broker", default="amqp://guest:guest@10.0.114.30:5672/%2F")
    ap.add_argument("--port", type=int, default=8000, help="stand: на каком порту притворяться бэком")
    ap.add_argument("--timeout", type=float, default=30.0, help="stand: сколько ждать ответа")
    ap.add_argument("--ws-backlog", type=int, default=32, help="stand: сколько ws кадров держать, старые выкидываем")
    ap.add_argument("--backend", default="http://127.0.0.1:8000", help="local: настоящий бэк")
    ap.add_argument("--token", default=None, help="local: X-Internal-Token к бэку (иначе env INTERNAL_TOKEN или .env)")
    ap.add_argument("--no-rewrite", action="store_true", help="local: не трогать rtsp_url в /internal/config")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        run_stand(args) if args.mode == "stand" else run_local(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
