#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid

import numpy as np
import pika
from tpu_pycloud_format import (MESSAGE_ID_ATTR_NAME, RESPONSE_ROUTING_KEYS_ATTR_NAME, TARGET_URI_ATTR_NAME, PyTask,
                                deserialize, serialize)

BROKER = "10.0.114.30"


def camera(program: str, fps: float, seconds: float, body: bytes, out: list, stop_at: float) -> None:
    conn = pika.BlockingConnection(pika.ConnectionParameters(BROKER, 5672, credentials=pika.PlainCredentials("guest", "guest")))
    ch = conn.channel()
    q = f"cam_{uuid.uuid4().hex[:10]}"
    ch.queue_declare(queue=q, exclusive=True)
    ch.queue_bind(exchange="tpu.direct", queue=q, routing_key=q)
    period, nxt = 1 / fps, time.perf_counter()
    while time.perf_counter() < stop_at:
        now = time.perf_counter()
        if now < nxt:
            time.sleep(nxt - now)
        nxt += period
        tid = uuid.uuid4().hex
        t0 = time.perf_counter()
        ch.basic_publish(exchange="tpu.direct", routing_key="input", body=body, properties=pika.BasicProperties(headers={
            TARGET_URI_ATTR_NAME: f"file://{program}?raw=False",
            RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([q]), MESSAGE_ID_ATTR_NAME: tid}))
        for m, p, b in ch.consume(q, auto_ack=True, inactivity_timeout=0.1):
            if m is not None and p.headers.get(MESSAGE_ID_ATTR_NAME) == tid:
                w = deserialize(b)[2]
                out.append((time.perf_counter() - t0, w["inference_time"], w["status_code"]))
                break
            if time.perf_counter() - t0 > 10:
                out.append((None, None, -1))
                break
        nxt = max(nxt, time.perf_counter())
    ch.cancel()
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", required=True)
    ap.add_argument("--cams", type=int, nargs="+", default=[1, 2, 4])
    ap.add_argument("--fps", type=float, default=6)
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--pause", type=float, default=5, help="между прогонами, стенд общий")
    args = ap.parse_args()

    body = serialize(PyTask({"images": np.random.default_rng(0).random((1, 3, 640, 640), dtype=np.float32)}))
    rows = []
    for n in args.cams:
        results = [[] for _ in range(n)]
        stop_at = time.perf_counter() + args.seconds
        threads = [threading.Thread(target=camera, args=(args.program, args.fps, args.seconds, body, results[i], stop_at))
                   for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok = [r for cam in results for r in cam if r[0] is not None and r[2] == 0]
        lat = np.array([r[0] for r in ok]) * 1000
        tpu = np.array([r[1] for r in ok]) * 1000
        row = {"cams": n, "fps_target": args.fps, "fps_per_cam": round(len(ok) / args.seconds / n, 2),
               "fps_total": round(len(ok) / args.seconds, 1), "errors": sum(len(c) for c in results) - len(ok),
               "latency_ms_p50": round(float(np.percentile(lat, 50)), 0), "latency_ms_p95": round(float(np.percentile(lat, 95)), 0),
               "tpu_ms_mean": round(float(tpu.mean()), 2)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        time.sleep(args.pause)
    print("| камер | цель, к/с на камеру | факт, к/с на камеру | всего к/с | задержка p50, мс | p95, мс | tpu, мс | ошибки |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['cams']} | {r['fps_target']} | {r['fps_per_cam']} | {r['fps_total']} | {r['latency_ms_p50']} | "
              f"{r['latency_ms_p95']} | {r['tpu_ms_mean']} | {r['errors']} |")


if __name__ == "__main__":
    main()
