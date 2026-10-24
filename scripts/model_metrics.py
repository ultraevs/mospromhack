#!/usr/bin/env python3
# метрики для жюри: P/R/F1 при рабочем пороге, mAP50, mAP50-95, float32 (onnx) и int8 (симуляция sdk = tpu)
# пороги: float из model.yaml, int8 из linq/meta.yaml (если там нет — как в воркере, порог float)
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_map import BATCH, average_precision  # noqa: E402
from scripts.linq_eval import FloatSession, QuantSession  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402
from worker.app.postprocess import decode, iou_matrix, preprocess  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IOUS = np.linspace(0.5, 0.95, 10)


# как match из eval_map, но совпадение сразу на 10 порогах iou: tp shape (n_det, 10)
# cls_map: класс модели -> id в разметке (для people на чужой разметке)
def match_iou(session, spec, images, labels_dir, cls_map, tag=""):
    low = dataclasses.replace(spec, thresholds=np.full(spec.nc, 0.001, np.float32))
    name = session.get_inputs()[0].name
    stats = {c: ([], []) for c in cls_map}
    n_gt = {c: 0 for c in cls_map}
    t0 = time.time()
    for start in range(0, len(images), BATCH):
        chunk = images[start:start + BATCH]
        batch = np.zeros((len(chunk), 3, spec.input_size, spec.input_size), np.float32)
        infos, sizes = [], []
        for i, path in enumerate(chunk):
            image = cv2.imread(str(path))
            sizes.append(image.shape[:2])
            infos.append(preprocess(image, spec.input_size, out=batch[i])[1])
        outputs = session.run(None, {name: batch})[0]
        for i, path in enumerate(chunk):
            dets = decode(outputs[i].astype(np.float32), low, infos[i])
            h, w = sizes[i]
            label = labels_dir / (path.stem + ".txt")
            rows = np.loadtxt(label, ndmin=2) if label.exists() and label.stat().st_size else np.zeros((0, 5))
            gt_cls = rows[:, 0].astype(int)
            cx, cy, bw, bh = rows[:, 1] * w, rows[:, 2] * h, rows[:, 3] * w, rows[:, 4] * h
            gt = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
            for c, lc in cls_map.items():
                g = gt[gt_cls == lc]
                n_gt[c] += len(g)
                idx = np.flatnonzero(dets.cls == c)
                idx = idx[np.argsort(-dets.conf[idx])]
                tp = np.zeros((len(idx), len(IOUS)), bool)
                if len(g) and len(idx):
                    iou = iou_matrix(dets.xyxy[idx], g)
                    # жадно по убыванию уверенности, отдельно на каждом пороге
                    for t, thr in enumerate(IOUS):
                        used = np.zeros(len(g), bool)
                        for k in range(len(idx)):
                            cand = np.where(used, -1, iou[k])
                            j = int(np.argmax(cand))
                            if cand[j] >= thr:
                                used[j] = True
                                tp[k, t] = True
                stats[c][0].append(tp)
                stats[c][1].extend(dets.conf[idx].tolist())
        done = start + len(chunk)
        print(f"  {tag} {done}/{len(images)} ({time.time() - t0:.0f} с)", flush=True)
    return {c: (np.concatenate(stats[c][0]) if stats[c][0] else np.zeros((0, len(IOUS)), bool),
                np.array(stats[c][1])) for c in cls_map}, n_gt


def summarize(tp, conf, n_gt, thr):
    ap = [average_precision(tp[:, t], conf, n_gt) for t in range(len(IOUS))]
    keep = conf >= thr
    hit = int(tp[keep, 0].sum())
    p = hit / max(int(keep.sum()), 1)
    r = hit / max(n_gt, 1)
    f1 = 2 * p * r / max(p + r, 1e-9)
    return {"thr": round(float(thr), 3), "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3),
            "map50": round(ap[0], 3), "map50_95": round(float(np.mean(ap)), 3), "boxes": int(n_gt)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True, type=Path, help="папка с images/val и labels/val")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--only", default="", help="класс=id_в_разметке, напр. person=0 для people на val ppe")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "linq" / "metrics.json")
    args = ap.parse_args()

    spec = load_model_spec(ROOT / "models" / args.model)
    linq_dir = ROOT / "models" / args.model / "linq"
    meta = yaml.safe_load((linq_dir / "meta.yaml").read_text(encoding="utf-8"))
    images = sorted((args.data / "images" / "val").iterdir())
    images = images[:: max(1, len(images) // args.limit)][:args.limit]
    labels = args.data / "labels" / "val"
    if args.only:
        n, lc = args.only.split("=")
        cls_map = {spec.class_id(n): int(lc)}
    else:
        cls_map = {c: c for c in range(spec.nc)}
    int8_thr = meta.get("thresholds") or {}

    result = {"data": str(args.data), "images": len(images), "iou": "0.5 для P/R/F1 и mAP50, 0.5:0.95 шаг 0.05",
              "float32": {}, "int8": {}}
    for kind, session in (("float32", FloatSession(spec.onnx_path)), ("int8", QuantSession(linq_dir, meta))):
        stats, n_gt = match_iou(session, spec, images, labels, cls_map, f"{args.model} {kind}")
        for c in cls_map:
            name = spec.classes[c]
            if n_gt[c] == 0:
                continue
            thr = spec.thresholds[c] if kind == "float32" else int8_thr.get(name, spec.thresholds[c])
            result[kind][name] = summarize(*stats[c], n_gt[c], thr)

    print(f"\n{args.model}: {len(images)} картинок val ({args.data})")
    print("| класс | рамок | порог f32 / int8 | P | R | F1 | mAP50 | mAP50-95 |")
    print("|---|---|---|---|---|---|---|---|")
    for name, f in result["float32"].items():
        q = result["int8"][name]
        cell = lambda k: f"{f[k]:.3f} / {q[k]:.3f}"  # noqa: E731
        print(f"| {name} | {f['boxes']} | {cell('thr')} | {cell('precision')} | {cell('recall')} | {cell('f1')} "
              f"| {cell('map50')} | {cell('map50_95')} |")
    for kind in ("float32", "int8"):
        vals = result[kind].values()
        result[kind + "_mean"] = {k: round(float(np.mean([v[k] for v in vals])), 3) for k in ("map50", "map50_95")}
    print(f"среднее float32 {result['float32_mean']}, int8 {result['int8_mean']}")

    allres = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    allres[args.model] = result
    args.out.write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
