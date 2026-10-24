#!/usr/bin/env python3
# пороги float по max F1 на val (onnx), как linq_thresholds.py для int8. только печать/json, model.yaml не трогаем
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.eval_map as em  # noqa: E402
from scripts.eval_map import match  # noqa: E402
from scripts.linq_thresholds import best_f1  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["ppe", "heads", "hazard"])
    ap.add_argument("--merged", type=Path, default=Path("D:/hak/ppe-train/ppe-train/merged"))
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--out", type=Path, help="json с порогами")
    args = ap.parse_args()
    ort.preload_dlls()
    res = {}
    for m in args.models:
        spec = load_model_spec(ROOT / "models" / m)
        images = sorted((args.merged / m / "images" / "val").iterdir())
        images = images[:: max(1, len(images) // args.limit)][:args.limit]
        dyn = spec.onnx_path.with_name("best.dynamic.onnx")
        em.BATCH = 8 if dyn.exists() else 1  # у heads нет dynamic, гоним по одной
        sess = ort.InferenceSession(str(dyn if dyn.exists() else spec.onnx_path), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        stats, n_gt, _ = match(sess, spec, images, args.merged / m / "labels" / "val")
        res[m] = {}
        print(f"{m}: {len(images)} картинок val")
        for c, name in enumerate(spec.classes):
            if n_gt[c] == 0 or not stats[c][0]:
                continue
            r = best_f1(np.array(stats[c][0], bool), np.array(stats[c][1]), int(n_gt[c]))
            r["rule"], r["boxes"], r["was"] = "max F1, float", int(n_gt[c]), round(float(spec.thresholds[c]), 3)
            res[m][name] = r
            print(f"  {name}: {r['was']:.3f} -> {r['conf']:.3f} (P {r['precision']:.2f} / R {r['recall']:.2f}, рамок {n_gt[c]})")
    if args.out:
        args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
