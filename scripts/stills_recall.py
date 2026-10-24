#!/usr/bin/env python3
# стоп-кадры как у жюри: каждая картинка = неподвижная камера N секунд, весь пайплайн воркера (ppe/people/heads/hazard,
# плитки, проход по головам, правила) без backend. кадр одинаковый, поэтому детекцию считаем раз на картинку,
# а правила гоняем по всем кадрам с растущим ts — время симулированное, ждать не надо.
# итог: recall по картинкам для no_helmet/no_vest/no_mask и ложные на чистых, при порогах float x1.0/x0.8/x0.6
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import ROOT, get_settings, load_config  # noqa: E402
from shared.schemas import WorkerCamera, WorkerConfig  # noqa: E402
from worker.app.capture import Frame  # noqa: E402
from worker.app.detector import create_detector  # noqa: E402
from worker.app.main import CameraState, Worker  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402
from worker.app.pipeline import check_classes  # noqa: E402

log = logging.getLogger("stills")
DATA = Path("D:/hak/ppe-train/ppe-train/merged/ppe")
CLS = ["person", "helmet", "no_helmet", "vest", "mask", "no_mask", "vehicle"]
TYPES = ["no_helmet", "no_vest", "no_mask"]
SCALED = ("ppe", "heads")  # пороги каких моделей крутим (каски/жилеты/маски)


class Backend:
    def __init__(self):
        self.events: list[str] = []

    def post_event(self, body, snapshot=None, clean=None):
        self.events.append(body.type.value)
        return SimpleNamespace(id=str(len(self.events)))

    def close_event(self, event_id, ended_at) -> None:
        pass

    def upload_clip(self, event_id, path) -> None:
        pass

    def close(self) -> None:
        pass


class Sender:
    connected = True

    def send(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass


class NoEvidence:
    # клипы и буфер кадров тут не нужны
    def add_frame(self, *a) -> None:
        pass

    def start_clip(self, *a) -> None:
        pass

    def pending(self) -> int:
        return 0


class StillReader:
    online, error = True, None

    def __init__(self, frame):
        self.frame = frame

    def latest(self):
        return self.frame

    def stop(self, wait=True) -> None:
        pass

    def read_fps(self) -> float:
        return 6.0


def inside(c, box) -> bool:
    return box[0] <= c[0] <= box[2] and box[1] <= c[1] <= box[3]


# разметка yolo -> какие нарушения на картинке. no_vest = человек без жилета (центр жилета внутри рамки), не мелкий
def gt(label: Path, w: int, h: int, min_h: float) -> dict | None:
    rows = [l.split() for l in label.read_text().splitlines() if l.strip()] if label.exists() else []
    boxes: dict[str, list] = {c: [] for c in CLS}
    for r in rows:
        c, cx, cy, bw, bh = int(r[0]), *map(float, r[1:5])
        boxes[CLS[c]].append(((cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h))
    if not boxes["person"]:
        return None
    vests = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in boxes["vest"]]
    no_vest = any(not any(inside(v, p) for v in vests) and p[3] - p[1] >= min_h for p in boxes["person"])
    all_vest = all(any(inside(v, p) for v in vests) for p in boxes["person"])
    out = {"no_helmet": bool(boxes["no_helmet"]), "no_vest": no_vest, "no_mask": bool(boxes["no_mask"])}
    out["clean"] = not any(out.values()) and all_vest and len(boxes["helmet"]) >= len(boxes["person"])
    out["persons"] = len(boxes["person"])
    return out


def pick(n_viol: int, n_clean: int, width: int, min_h: float, seed: int) -> list[dict]:
    items = []
    for split in ("test", "val"):  # в test всего 82, добираем из val (на нём выбирали пороги, но не учили)
        for img in sorted((DATA / "images" / split).iterdir()):
            im = cv2.imread(str(img))
            if im is None:
                continue
            k = width / im.shape[1]
            g = gt(DATA / "labels" / split / (img.stem + ".txt"), width, im.shape[0] * k, min_h)
            if g:
                items.append({"path": str(img), "split": split, **g})
    rnd = random.Random(seed)
    rnd.shuffle(items)
    items.sort(key=lambda x: x["split"] != "test")  # сначала test
    viol = [x for x in items if any(x[t] for t in TYPES)][:n_viol]
    clean = [x for x in items if x["clean"]][:n_clean]
    return viol + clean


def run_image(worker: Worker, cam: WorkerCamera, image: np.ndarray, seconds: float, fps: float) -> list[str]:
    worker.client.events.clear()
    state = CameraState(cam, StillReader(None), NoEvidence())
    worker.cameras = {cam.id: state}
    t0 = int(time.time() * 1000)
    frame = Frame(image, t0, 1)
    dets = worker._detect([(state, frame)])[0]
    if worker.config.worker.crop_pass.head.enabled:
        dets = worker._head_pass(image, dets, worker.specs["ppe"], 1.0)
    hz = worker._infer_frames("hazard", [image])[0] if "hazard" in worker.specs else None
    h, w = image.shape[:2]
    period = 1000 / fps
    hz_every = max(1, round(fps / worker.config.worker.hazard_fps))
    for i in range(int(seconds * fps)):
        f = Frame(image, t0 + int(i * period), i + 1)
        worker._handle(state, f, dets, worker.specs["ppe"])
        if hz is not None and i % hz_every == 0:
            worker._queue(state, f, state.pipeline.process_hazard(hz, worker.specs["hazard"], w, h, f.ts_ms,
                                                                  worker.remote.rules))
            worker.flush_events()
    return list(worker.client.events)


def table(results: dict, items: list[dict]) -> list[str]:
    lines = ["| порог | " + " | ".join(f"{t}: recall (n) | {t}: ложные (n)" for t in TYPES) + " |",
             "|---|" + "---|---|" * len(TYPES)]
    for scale, r in results.items():
        cells = []
        for t in TYPES:
            pos = [e for e, x in zip(r, items) if x[t]]
            neg = [e for e, x in zip(r, items) if x["clean"]]
            rec = sum(t in e for e in pos) / len(pos) if pos else 0
            fp = sum(t in e for e in neg) / len(neg) if neg else 0
            cells += [f"{rec:.2f} ({len(pos)})", f"{fp:.2f} ({len(neg)})"]
        lines.append(f"| x{scale} | " + " | ".join(cells) + " |")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--viol", type=int, default=150)
    ap.add_argument("--clean", type=int, default=50)
    ap.add_argument("--seconds", type=float, default=8)
    ap.add_argument("--fps", type=float, default=6)
    ap.add_argument("--scales", type=float, nargs="+", default=[1.0, 0.8, 0.6])
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("worker").setLevel(logging.WARNING)

    settings = get_settings()
    config = load_config(settings.config_path)
    config.worker.ppe_fps = args.fps
    width = config.worker.decode.width  # декодер приводит кадр к этой ширине
    items = pick(args.viol, args.clean, width, config.rules.no_vest.min_person_height_px, args.seed)
    log.info("картинок %s: %s", len(items), {t: sum(x[t] for x in items) for t in TYPES + ["clean"]})
    images = []
    for x in items:
        im = cv2.imread(x["path"])
        images.append(cv2.resize(im, (width, round(im.shape[0] * width / im.shape[1]))))

    base = load_all_specs(settings.models_dir)
    check_classes(base["ppe"], base.get("hazard"))
    detector = create_detector("onnx", base, None, config.worker.precision)
    log.info("детектор %s, модели %s, плитки %s", detector.device, sorted(base), config.worker.tiles)
    cam = WorkerCamera(id=1, name="still", rtsp_url="still://", required_ppe=["helmet", "vest", "mask"])
    results: dict = {}
    for scale in args.scales:
        specs = {n: replace(s, thresholds=s.thresholds * scale) if n in SCALED else s for n, s in base.items()}
        worker = Worker(config, specs, detector, Backend(), Sender())
        worker.remote = WorkerConfig(version=1, cameras=[cam], rules=config.rules)
        worker.period_s = 1 / args.fps
        out = []
        t0 = time.monotonic()
        for i, im in enumerate(images):
            out.append(run_image(worker, cam, im, args.seconds, args.fps))
            if (i + 1) % 25 == 0:
                log.info("x%s: %s/%s, %.0f c", scale, i + 1, len(images), time.monotonic() - t0)
        for pool in (worker._pool, worker._cpu, worker._side, worker._detect_pool):
            pool.shutdown(wait=False, cancel_futures=True)
        results[scale] = out
        print("\n".join(table({scale: out}, items)[2:]), flush=True)

    lines = table(results, items)
    print("\n" + "\n".join(lines))
    dump = {"seconds": args.seconds, "fps": args.fps, "required_ppe": cam.required_ppe, "scaled_models": SCALED,
            "tiles": config.worker.tiles, "table": lines,
            "images": [{**x, "events": {str(s): sorted(set(results[s][i])) for s in results}} for i, x in enumerate(items)]}
    out = ROOT / "data" / "stills_recall.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"отчет: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
