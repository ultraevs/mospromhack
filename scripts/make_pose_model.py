#!/usr/bin/env python3
# поза: стоковая yolov8n-pose (coco, 17 точек). нужна для лежит/переноска/телефон у уха, не учим
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.config import get_settings  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="yolov8n-pose.pt")
    args = ap.parse_args()
    from ultralytics import YOLO

    out = get_settings().models_dir / "pose"
    out.mkdir(parents=True, exist_ok=True)
    weights = Path(args.weights).resolve() if Path(args.weights).exists() else args.weights
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        model = YOLO(str(weights))
        onnx_path = Path(model.export(format="onnx", imgsz=640, opset=17, simplify=True))
        shutil.copy(onnx_path, out / "best.onnx")
        os.chdir(ROOT)
    spec = {
        "model": "pose",
        "source": str(args.weights),
        "input": {"name": "images", "shape": [1, 3, 640, 640], "layout": "NCHW", "color": "RGB", "scale": "0..1",
                  "letterbox": True},
        "output": {"name": "output0", "shape": [1, 4 + 1 + 17 * 3, 8400]},
        "nms_iou": 0.7,
        "classes": ["person"],
        "keypoints": 17,
        "thresholds": {"person": 0.4},  # вручную, как у people
    }
    (out / "model.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"готово: {out}")


if __name__ == "__main__":
    main()
