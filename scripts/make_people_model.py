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

# cell phone под правило телефона, порог 0.35 подобран вручную на глаз, на разметке не мерили
KEEP = {"person": 0.5, "car": 0.6, "truck": 0.6, "bus": 0.6, "motorcycle": 0.6, "cell phone": 0.35}


# из 80 классов coco оставляем только KEEP: режем выходные каналы последних conv в cv3.
# на tpu выход классов 5x8448 вместо 80x8448, скоры те же
def prune_classes(model, keep: list[str]) -> None:
    import torch

    det = model.model.model[-1]
    idx = [i for i in range(det.nc) if model.names[i] in keep]
    for branch in det.cv3:
        old = branch[-1]
        new = torch.nn.Conv2d(old.in_channels, len(idx), 1)
        with torch.no_grad():
            new.weight.copy_(old.weight[idx])
            new.bias.copy_(old.bias[idx])
        branch[-1] = new
    names = [model.names[i] for i in idx]
    det.nc, det.no = len(idx), len(idx) + det.reg_max * 4
    model.model.names = dict(enumerate(names))
    model.model.yaml["nc"] = len(idx)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="yolov8s.pt")
    ap.add_argument("--all-classes", action="store_true", help="не резать голову до KEEP")
    args = ap.parse_args()
    from ultralytics import YOLO

    out = get_settings().models_dir / "people"
    out.mkdir(parents=True, exist_ok=True)
    # ultralytics качает веса в cwd, пусть мусорит во временной папке
    weights = Path(args.weights).resolve() if Path(args.weights).exists() else args.weights
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        model = YOLO(str(weights))
        if not args.all_classes:
            prune_classes(model, list(KEEP))
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
        # если голова не резана, остальные классы coco не нужны, merge_people их все равно выкидывает
        "thresholds": {n: KEEP.get(n, 0.99) for n in names},
    }
    (out / "model.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"готово: {out}")


if __name__ == "__main__":
    main()
