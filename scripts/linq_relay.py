#!/usr/bin/env python3
# посредник на стенде: ноутбук шлёт jpeg по vpn, тут кадр идёт в облако tpu (та же сеть), назад рамки json.
#   serve — на стенде: linq_remote.py sh "nohup python scripts/linq_relay.py serve --workers 4 > /auto/cache/$LINQ_LOGIN/relay.log 2>&1 &"
#   bench — с ноутбука (внутри linq-vpn): LINQ_LOGIN=... python scripts/linq_relay.py bench --par 1 2 4
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from worker.app.detector.relay import (pack_reply, relay_queue, unpack_request)  # noqa: E402

log = logging.getLogger("relay")
QUEUE_ARGS = {"x-expires": 600000}  # посредник умер — очередь сама пропадёт через 10 мин


def make_detector(models: list[str], raw: bool | None):
    from shared.config import load_config
    from worker.app.detector.linq import LinqDetector
    from worker.app.model_spec import load_all_specs

    specs = {n: s for n, s in load_all_specs(ROOT / "models").items() if n in models}
    cfg = load_config(ROOT / "config.yaml").worker.linq
    login = os.environ["LINQ_LOGIN"]
    det = LinqDetector(specs, transport="cloud", broker=cfg.broker, port=cfg.port,
                       program_dir=cfg.program_dir.replace("{login}", login), timeout_s=cfg.timeout_s,
                       batch=cfg.batch, raw=cfg.raw if raw is None else raw)
    return det, specs


def run_model(det, specs, model: str, images: list[np.ndarray]) -> list:
    # ровно как воркер (_infer_packed/_infer_frames), только кадр уже в размере входа — letterbox пустой
    from dataclasses import replace

    from worker.app.postprocess import decode, preprocess
    spec = specs[model]
    if model in det.thresholds:
        spec = replace(spec, thresholds=det.thresholds[model])
    if det.raw:
        prepared = [det.prepare(model, im) for im in images]
    else:
        prepared = [preprocess(im, spec.input_size) for im in images]
    outputs = []
    step = det.batch_limit(model) or len(prepared)
    for i in range(0, len(prepared), step):
        outputs.extend(det.infer(model, np.stack([x for x, _ in prepared[i:i + step]])))
    out = []
    for o, (_, info) in zip(outputs, prepared):
        d = decode(o, spec, info)
        out.append([[*map(lambda v: round(float(v), 1), b), round(float(c), 4), int(k)]
                    for b, c, k in zip(d.xyxy, d.conf, d.cls)])
    return out


def serve(args) -> None:
    import pika

    det, specs = make_detector(args.models, args.raw)
    queue = relay_queue(os.environ["LINQ_LOGIN"])
    cfg = det.runner.params
    log.info("посредник готов: модели %s, raw %s, обработчиков %d", list(specs), det.raw, args.workers)

    def loop(n: int) -> None:
        while True:
            try:
                conn = pika.BlockingConnection(pika.ConnectionParameters(cfg.host, cfg.port, credentials=cfg.credentials,
                                                                         heartbeat=600))
                ch = conn.channel()
                ch.queue_declare(queue=queue, arguments=QUEUE_ARGS)
                ch.basic_qos(prefetch_count=1)
                for method, props, body in ch.consume(queue):
                    t0 = time.perf_counter()
                    try:
                        model, images = unpack_request(body, props.headers or {})
                        reply = pack_reply(props.correlation_id, run_model(det, specs, model, images),
                                           {"ms": round((time.perf_counter() - t0) * 1000, 1)})
                    except Exception as e:  # noqa: BLE001
                        log.exception("обработчик %d: запрос упал", n)
                        reply = b'{"id":"%s","error":"%s"}' % (str(props.correlation_id).encode(),
                                                                type(e).__name__.encode())
                    if props.reply_to:
                        ch.basic_publish(exchange="", routing_key=props.reply_to, body=reply,
                                         properties=pika.BasicProperties(correlation_id=props.correlation_id))
                    ch.basic_ack(method.delivery_tag)
                    if args.verbose:
                        log.info("%d: %s %d кадр(ов) %.0f мс", n, props.headers.get("model"),
                                 len(images), (time.perf_counter() - t0) * 1000)
            except Exception as e:  # noqa: BLE001
                log.error("обработчик %d: %s, переподключаюсь", n, e)
                time.sleep(2)

    for n in range(args.workers):
        threading.Thread(target=loop, args=(n,), daemon=True).start()
    while True:
        time.sleep(3600)


def frames(video: Path, n: int, width: int = 1280) -> list[np.ndarray]:
    import cv2
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or n
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i * max(1, total // n))
        ok, img = cap.read()
        if not ok:
            break
        h, w = img.shape[:2]
        out.append(cv2.resize(img, (width, round(h * width / w))))
    return out


def bench(args) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from worker.app.crops import tile_spans
    from worker.app.detector.relay import RelayDetector, login_from_env
    from worker.app.model_spec import load_all_specs

    specs = {n: s for n, s in load_all_specs(ROOT / "models").items() if n in args.models}
    det = RelayDetector(specs, broker=args.broker, timeout_s=args.timeout, login=login_from_env())
    imgs = frames(Path(args.video), 20)
    side = ThreadPoolExecutor(8)

    # кадр как у воркера: ppe + people + heads по двум плиткам параллельно; hazard отдельно, он раз в секунду
    def one(img):
        jobs = []
        for m in ("people", "heads"):
            if m not in specs:
                continue
            ims = [img[:, a:b] for a, b in tile_spans(img.shape[1], img.shape[0])] if m == "heads" else [img]
            jobs.append(side.submit(lambda m=m, ims=ims: det.infer(m, np.stack([det.prepare(m, x)[0] for x in ims]))))
        det.infer("ppe", det.prepare("ppe", img)[0][None])
        for j in jobs:
            j.result()

    one(imgs[0])  # прогрев
    for par in args.par:
        for t in det.timings.values():
            t.update(frames=0, lost=0, roundtrip_ms=0.0, bytes_out=0, bytes_in=0, server_ms=0.0)
        lat, stop = [], time.monotonic() + args.seconds

        def cam(k: int) -> None:
            i = k
            while time.monotonic() < stop:
                t0 = time.perf_counter()
                one(imgs[i % len(imgs)])
                lat.append(time.perf_counter() - t0)
                i += 1

        t0 = time.monotonic()
        threads = [threading.Thread(target=cam, args=(k,)) for k in range(par)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        took = time.monotonic() - t0
        n = len(lat)
        out = sum(t["bytes_out"] for t in det.timings.values()) / max(n, 1) / 1024
        inn = sum(t["bytes_in"] for t in det.timings.values()) / max(n, 1) / 1024
        lost = sum(t["lost"] for t in det.timings.values())
        per = " ".join(f"{m}={t['roundtrip_ms'] / max(t['frames'], 1):.0f}мс(стенд {t['server_ms'] / max(t['frames'], 1):.0f})" for m, t in det.timings.items() if t["frames"])
        print(f"параллельно {par}: {n} кадров за {took:.1f} с = {n / took:.2f} к/с всего, {n / took / par:.2f} на поток; "
              f"задержка кадра p50 {np.median(lat) * 1000:.0f} мс p95 {np.percentile(lat, 95) * 1000:.0f} мс; "
              f"трафик на кадр {out:.0f} КБ туда / {inn:.1f} КБ назад; потеряно {lost}; запрос: {per}", flush=True)
    det.close()
    side.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description="посредник linq: jpeg по vpn -> tpu на стенде -> рамки json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--models", nargs="+", default=["ppe", "heads", "hazard", "people"])
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--raw", type=lambda s: s == "1", default=None, help="1/0, по умолчанию как в config.yaml")
    p.add_argument("--verbose", action="store_true")
    p = sub.add_parser("bench")
    p.add_argument("--models", nargs="+", default=["ppe", "heads", "people"])
    p.add_argument("--broker", default="10.0.114.30")
    p.add_argument("--video", required=True)
    p.add_argument("--par", nargs="+", type=int, default=[1, 2, 4])
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--timeout", type=float, default=10)
    args = ap.parse_args()
    serve(args) if args.cmd == "serve" else bench(args)


if __name__ == "__main__":
    main()
