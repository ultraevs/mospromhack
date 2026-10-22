#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import ROOT, get_settings  # noqa: E402
from worker.app.detector import create_detector  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402
from worker.app.postprocess import decode, preprocess  # noqa: E402

GREEN, YELLOW = (80, 220, 80), (0, 220, 255)


def main() -> None:
    settings = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="warehouse.mp4")
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--low", type=float, default=0.25)
    ap.add_argument("--width", type=int, default=1280, help="ширина кадра, как в обработке; 0 — исходная")
    ap.add_argument("--out", default="report")
    args = ap.parse_args()

    spec = load_all_specs(settings.models_dir)["ppe"]
    person = spec.class_id("person")
    low = spec.thresholds.copy()
    low[person] = args.low
    low_spec = dataclasses.replace(spec, thresholds=low)
    detector = create_detector("onnx", {"ppe": spec})

    cap = cv2.VideoCapture(str(settings.data_dir / "videos" / args.video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    out_dir = ROOT / args.out
    out_dir.mkdir(exist_ok=True)
    stem = Path(args.video).stem
    print(f"{args.video}: {total} кадров, порог person из model.yaml {spec.threshold('person'):.3f}, низкий порог {args.low}")
    print("| кадр | время, с | найдено при пороге model.yaml | найдено при низком пороге | оценки найденных | файл |")
    print("|---|---|---|---|---|---|")
    for k in range(args.frames):
        index = round((k + 0.5) * total / args.frames)
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = cap.read()
        if not ok:
            continue
        if args.width and image.shape[1] > args.width:
            h = round(image.shape[0] * args.width / image.shape[1]) // 2 * 2
            image = cv2.resize(image, (args.width, h), interpolation=cv2.INTER_AREA)
        tensor, info = preprocess(image, spec.input_size)
        output = detector.infer("ppe", tensor[None])[0]
        strict = decode(output, spec, info)
        loose = decode(output, low_spec, info)
        strict_boxes = strict.xyxy[strict.cls == person]
        loose_idx = np.flatnonzero(loose.cls == person)
        canvas = image.copy()
        confs = []
        for i in loose_idx:
            x1, y1, x2, y2 = (int(v) for v in loose.xyxy[i])
            conf = float(loose.conf[i])
            confs.append(conf)
            color = GREEN if conf >= spec.threshold("person") else YELLOW
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
            cv2.putText(canvas, f"{conf:.2f}", (x1, max(14, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        label = f"frame {index}  model.yaml: {len(strict_boxes)}  conf>={args.low}: {len(loose_idx)}"
        cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 30), (0, 0, 0), -1)
        cv2.putText(canvas, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        name = f"{stem}_person_{k + 1:02d}_frame{index}.jpg"
        cv2.imwrite(str(out_dir / name), canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(f"| {index} | {index / fps:.1f} | {len(strict_boxes)} | {len(loose_idx)} | "
              f"{' '.join(f'{c:.2f}' for c in sorted(confs, reverse=True))} | {args.out}/{name} |")
    cap.release()


if __name__ == "__main__":
    main()
