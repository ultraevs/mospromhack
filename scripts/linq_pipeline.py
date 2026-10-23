#!/usr/bin/env python3
# замер всего воркера на стенде без backend: зацикленные ролики -> декод -> ppe/people/hazard на tpu -> правила -> события.
# backend подменён заглушкой, рамки и события никуда не уходят, только считаются. так видно потолок железа рядом с tpu
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import worker.app.capture as capture  # noqa: E402
from shared.config import ROOT, get_settings, load_config  # noqa: E402
from shared.schemas import WorkerCamera, WorkerConfig  # noqa: E402
from shared.sync import loop_input  # noqa: E402
from worker.app.detector import create_detector  # noqa: E402
from worker.app.main import Worker  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402
from worker.app.pipeline import check_classes  # noqa: E402

log = logging.getLogger("pipeline")


class Backend:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.events: list[dict] = []
        self.stats: list[dict] = []

    def get_config(self) -> WorkerConfig:
        return self.cfg

    def post_event(self, body, snapshot=None, clean=None):
        now = time.time() * 1000
        self.events.append({"type": body.type.value, "camera": body.camera_id, "t": now,
                            "latency_ms": now - body.captured_ts if body.captured_ts else None})
        return SimpleNamespace(id=str(len(self.events)))

    def close_event(self, event_id, ended_at) -> None:
        pass

    def upload_clip(self, event_id, path) -> None:
        pass

    def post_stats(self, stats: dict) -> None:
        self.stats.append({**stats, "t": time.monotonic()})

    def close(self) -> None:
        pass


class Sender:
    connected = True

    def __init__(self):
        self.sent = 0

    def send(self, text: str) -> None:
        self.sent += 1

    def stop(self) -> None:
        pass


def summary(stats: list[dict], events: list[dict], cams: int, fps: float, seconds: float) -> dict:
    # первые секунды прогрев: подключение камер, первые запросы в облако
    tail = stats[len(stats) // 3:] or stats
    per_cam = [np.mean([c["proc_fps"] for c in s["cameras"].values()]) for s in tail if s["cameras"]]
    infer = {m: round(float(np.mean([s["inference"][m]["avg_ms"] for s in tail if m in s["inference"]])), 1)
             for m in {m for s in tail for m in s["inference"]}}
    cpu = [s["cpu_cores"]["worker"] + s["cpu_cores"]["decoders"] for s in tail]
    lat = [e["latency_ms"] for e in events if e["latency_ms"] is not None]
    loop = {}
    for s in tail:
        for k, v in s["loop_ms_per_s"].items():
            loop[k] = loop.get(k, 0) + v / len(tail)
    return {
        "cameras": cams, "target_fps": fps, "seconds": seconds,
        "proc_fps_per_cam": round(float(np.mean(per_cam)), 2) if per_cam else 0.0,
        "proc_fps_total": round(float(np.mean(per_cam)) * cams, 1) if per_cam else 0.0,
        "loop_load": round(float(np.mean([s["loop_load"] for s in tail])), 2),
        "cpu_cores": round(float(np.mean(cpu)), 2),
        "decoders_cores": round(float(np.mean([s["cpu_cores"]["decoders"] for s in tail])), 2),
        "inference_ms": infer,
        "crops_per_s": round(float(np.mean([s["crops_per_s"] for s in tail])), 1),
        "events": len(events),
        "event_latency_ms": {"p50": round(float(np.percentile(lat, 50))), "p95": round(float(np.percentile(lat, 95)))}
        if lat else None,
        "loop_ms_per_s": {k: round(v) for k, v in sorted(loop.items(), key=lambda kv: -kv[1])[:8]},
    }


def run_once(config, specs, detector, videos: list[str], cams: int, fps: float, seconds: float) -> dict:
    config.worker.ppe_fps = fps
    cameras = [WorkerCamera(id=i + 1, name=f"cam{i + 1}", rtsp_url=f"{capture.SYNC_SCHEME}{videos[i % len(videos)]}",
                            required_ppe=["helmet", "vest"]) for i in range(cams)]
    backend = Backend(WorkerConfig(version=1, cameras=cameras, rules=config.rules))
    worker = Worker(config, specs, detector, backend, Sender())
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        time.sleep(5)
        if backend.stats:
            s = backend.stats[-1]
            log.info("%s камер: к/с %s, загрузка цикла %s, cpu %s", cams,
                     [c["proc_fps"] for c in s["cameras"].values()], s["loop_load"], s["cpu_cores"])
    worker.stop()
    thread.join(timeout=10)
    for state in worker.cameras.values():
        state.reader.stop()
    worker._pool.shutdown(wait=False, cancel_futures=True)
    worker._cpu.shutdown(wait=False, cancel_futures=True)
    worker._side.shutdown(wait=False, cancel_futures=True)
    worker._clips.cleanup()
    return summary(backend.stats, backend.events, cams, fps, seconds)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, nargs="+", default=[1, 2, 4])
    ap.add_argument("--fps", type=float, default=6, help="целевая частота на камеру (ppe_fps)")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--videos", nargs="+", default=["factory_floor.mp4", "warehouse.mp4"])
    ap.add_argument("--detector", default="linq", help="linq (стенд) | onnx (для сверки у себя)")
    ap.add_argument("--tag", default="stand")
    ap.add_argument("--no-raw", action="store_true", help="старый путь: float32, пакует облако")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "pika", "worker.capture"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # на стенде ffmpeg 4.4, -readrate_initial_burst нет; синхронность с ui тут не нужна, крутим с начала
    capture.sync_input = lambda url, now=None: loop_input(capture.sync_path(url), None)

    settings = get_settings()
    config = load_config(settings.config_path)
    config.worker.detector = args.detector
    config.worker.decode.hwaccel = "none"
    # config.yaml на стенде мог остаться старым, raw задаём явно
    config.worker.linq.raw = not args.no_raw
    specs = load_all_specs(settings.models_dir)
    check_classes(specs["ppe"], specs.get("hazard"))
    linq = config.worker.linq.model_dump()
    linq["program_dir"] = linq["program_dir"].replace("{login}", settings.linq_login or os.environ.get("LINQ_LOGIN", ""))
    detector = create_detector(args.detector, specs, None, config.worker.precision, linq)
    log.info("детектор %s, модели %s", detector.device.replace(settings.linq_login or "-", "<login>"), sorted(specs))

    rows = []
    try:
        for cams in args.cams:
            rows.append(run_once(config, specs, detector, args.videos, cams, args.fps, args.seconds))
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    finally:
        close = getattr(detector, "close", None)
        if close:
            close()

    print(f"\n| камер | к/с на камеру (цель {args.fps:g}) | всего к/с | загрузка цикла | cpu, ядер | ppe мс | people мс | "
          "событий | задержка события p50/p95 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        lat = r["event_latency_ms"]
        lat_s = f"{lat['p50']}/{lat['p95']} мс" if lat else "-"
        print(f"| {r['cameras']} | {r['proc_fps_per_cam']} | {r['proc_fps_total']} | {r['loop_load']} | {r['cpu_cores']} | "
              f"{r['inference_ms'].get('ppe', '-')} | {r['inference_ms'].get('people', '-')} | {r['events']} | {lat_s} |")
    out = ROOT / "data" / "linq" / f"pipeline_{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nотчет: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
