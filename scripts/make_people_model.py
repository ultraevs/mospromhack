#!/usr/bin/env python3
# людей и технику ищет обычная coco yolov8s, наша ppe на дальних камерах людей теряет.
# сизы, огонь, курение остаются на своих моделях
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

KEEP = {"person": 0.3, "car": 0.4, "truck": 0.4, "bus": 0.4, "motorcycle": 0.4}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="yolov8s.pt")
    args = ap.parse_args()
    from ultralytics import YOLO

    out = get_settings().models_dir / "people"
    out.mkdir(parents=True, exist_ok=True)
    # ultralytics качает веса в cwd, пусть мусорит во временной папке
    weights = Path(args.weights).resolve() if Path(args.weights).exists() else args.weights
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        model = YOLO(str(weights))
        onnx_path = Path(model.export(format="onnx", imgsz=640, opset=17, simplify=True))
        shutil.copy(onnx_path, out / "best.onnx")
        names = [model.names[i] for i in range(len(model.names))]
        os.chdir(ROOT)
    spec = {
        "model": "people",
        "source": args.weights,
        "input": {"name": "images", "shape": [1, 3, 640, 640], "layout": "NCHW", "color": "RGB", "scale": "0..1",
                  "letterbox": True},
        "output": {"name": "output0", "shape": [1, 4 + len(names), 8400]},
        "nms_iou": 0.7,
        "classes": names,
        # остальные классы coco не нужны, merge_people их все равно выкидывает
        "thresholds": {n: KEEP.get(n, 0.99) for n in names},
    }
    (out / "model.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"готово: {out}")


if __name__ == "__main__":
    main()
