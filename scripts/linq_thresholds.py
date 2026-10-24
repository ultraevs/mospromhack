#!/usr/bin/env python3
# пороги под int8: у квантованной модели оценки ниже, чем у float, ранжирование то же (mAP не падает),
# а пороги из model.yaml подобраны под float и на tpu режут почти всё. берём max F1 по симуляции = tpu
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_map import match  # noqa: E402
from scripts.linq_eval import QuantSession  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def best_f1(tp: np.ndarray, conf: np.ndarray, n_gt: int) -> dict:
    order = np.argsort(-conf)
    tp, conf = tp[order], conf[order]
    tpc = np.cumsum(tp)
    p = tpc / np.arange(1, len(tp) + 1)
    r = tpc / max(n_gt, 1)
    f1 = 2 * p * r / np.maximum(p + r, 1e-9)
    i = int(f1.argmax())
    return {"conf": round(float(conf[i]), 3), "precision": round(float(p[i]), 3), "recall": round(float(r[i]), 3),
            "rule": "max F1, int8"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--merged", required=True, type=Path, help="папка merged с <model>/images/val и labels/val")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry", action="store_true", help="только напечатать, meta.yaml не трогать")
    args = ap.parse_args()

    spec = load_model_spec(ROOT / "models" / args.model)
    linq_dir = ROOT / "models" / args.model / "linq"
    meta_path = linq_dir / "meta.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    images = sorted((args.merged / args.model / "images" / "val").iterdir())
    images = images[:: max(1, len(images) // args.limit)][:args.limit]
    stats, n_gt, _ = match(QuantSession(linq_dir, meta), spec, images, args.merged / args.model / "labels" / "val")

    chosen = {}
    print(f"{args.model}: {len(images)} картинок val, порог float -> int8 (P / R при int8)")
    for c, name in enumerate(spec.classes):
        if n_gt[c] == 0 or not stats[c][0]:
            print(f"  {name}: нет разметки в выборке, оставляю порог float {spec.thresholds[c]:.3f}")
            continue
        chosen[name] = best_f1(np.array(stats[c][0], bool), np.array(stats[c][1]), int(n_gt[c]))
        print(f"  {name}: {spec.thresholds[c]:.3f} -> {chosen[name]['conf']:.3f} "
              f"(P {chosen[name]['precision']:.2f} / R {chosen[name]['recall']:.2f}, рамок {n_gt[c]})")
    if args.dry or not chosen:
        return
    meta["thresholds"] = {name: chosen[name]["conf"] if name in chosen else float(round(spec.thresholds[c], 3))
                          for c, name in enumerate(spec.classes)}
    meta["thresholds_info"] = chosen | {"images": len(images)}
    meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"записано в {meta_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
