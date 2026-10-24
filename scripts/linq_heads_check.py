#!/usr/bin/env python3
# сверка головы на tpu и onnx на одних кадрах ролика (плитки как в воркере)
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.app.crops import tile_spans  # noqa: E402
from worker.app.detector.linq import LinqDetector  # noqa: E402
from worker.app.detector.onnx_detector import OnnxDetector  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402
from worker.app.postprocess import decode, preprocess  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def frames(video: Path, times: list[float]) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(video))
    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if ok:
            out.append(img)
    return out


def show(d, names) -> str:
    keep = [(names[int(c)], round(float(s), 2)) for c, s in zip(d.cls, d.conf)]
    return f"{len(keep)} {sorted(keep, key=lambda x: -x[1])[:6]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="heads")
    ap.add_argument("--video", default="data/videos/solo-worker.mp4")
    ap.add_argument("--times", type=float, nargs="+", default=[3, 8, 15, 25])
    ap.add_argument("--no-raw", action="store_true")
    ap.add_argument("--login", default=os.environ.get("LINQ_LOGIN", ""))
    args = ap.parse_args()
    spec = load_model_spec(ROOT / "models" / args.model)
    names = spec.classes
    onnx = OnnxDetector({args.model: spec}, allow_cuda=False, prefer_dynamic=False)
    tpu = LinqDetector({args.model: spec}, transport="cloud", program_dir=f"/auto/cache/{args.login}", raw=not args.no_raw)
    qspec = dataclasses.replace(spec, thresholds=tpu.thresholds.get(args.model, spec.thresholds))
    for t, img in zip(args.times, frames(ROOT / args.video, args.times)):
        for x1, x2 in tile_spans(img.shape[1], img.shape[0]):
            tile = img[:, x1:x2]
            x, info = preprocess(tile, spec.input_size)
            a = decode(onnx.infer(args.model, x[None])[0], spec, info)
            packed, pinfo = tpu.prepare(args.model, tile) if tpu.raw else (x, info)
            raw = tpu.infer(args.model, packed[None])[0]
            b = decode(raw, qspec, pinfo)
            print(f"t={t} плитка {x1}: onnx {show(a, names)} | tpu {show(b, names)} | tpu max cls {raw[4:].max():.3f}", flush=True)
    tpu.close()


if __name__ == "__main__":
    main()
