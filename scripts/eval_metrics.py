#!/usr/bin/env python3
# P/R/mAP50/mAP50-95 по классам на val, float32 onnx на cpu (для слайда)
# P и R — при боевых порогах из model.yaml, mAP — по всем детекциям с conf>=0.001
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_map import average_precision  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402
from worker.app.postprocess import decode, iou_matrix, preprocess  # noqa: E402

IOUS = np.linspace(0.5, 0.95, 10)


def match(iou: np.ndarray, thr: float) -> np.ndarray:
    # жадно по убыванию conf, строки уже отсортированы
    iou = iou.copy()
    hit = np.zeros(len(iou), bool)
    for k in range(len(iou)):
        j = int(np.argmax(iou[k]))
        if iou[k, j] >= thr:
            hit[k] = True
            iou[:, j] = 0
    return hit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True, help="папка с images/val и labels/val")
    ap.add_argument("--models-dir", default="models")
    ap.add_argument("--classes", nargs="*", help="считать только эти (people на разметке ppe)")
    ap.add_argument("--gt-map", nargs="*", default=[], help="id_в_разметке:id_модели, напр. 0:0")
    ap.add_argument("--limit", type=int, default=0, help="сколько картинок val брать; 0 — все")
    args = ap.parse_args()

    spec = load_all_specs(Path(args.models_dir))[args.model]
    low = dataclasses.replace(spec, thresholds=np.full(spec.nc, 0.001, np.float32))
    # один поток, иначе ort съедает квоту стенда
    so = ort.SessionOptions()
    so.intra_op_num_threads = so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sess = ort.InferenceSession(str(spec.onnx_path), so, providers=["CPUExecutionProvider"])
    cv2.setNumThreads(1)
    name = sess.get_inputs()[0].name
    gmap = {int(a): int(b) for a, b in (s.split(":") for s in args.gt_map)}
    want = [spec.class_id(c) for c in args.classes] if args.classes else list(range(spec.nc))

    images = sorted((Path(args.data) / "images" / "val").iterdir())
    if args.limit:
        images = images[:: max(1, len(images) // args.limit)][:args.limit]
    labels = Path(args.data) / "labels" / "val"
    tp = {c: [] for c in want}  # [n_det, 10]
    conf = {c: [] for c in want}
    n_gt = dict.fromkeys(want, 0)
    for n, path in enumerate(images):
        image = cv2.imread(str(path))
        h, w = image.shape[:2]
        x, info = preprocess(image, spec.input_size)
        out = sess.run(None, {name: x[None] if x.ndim == 3 else x})[0]
        dets = decode(out[0].astype(np.float32), low, info)
        lab = labels / (path.stem + ".txt")
        rows = np.loadtxt(lab, ndmin=2) if lab.exists() and lab.stat().st_size else np.zeros((0, 5))
        cls = rows[:, 0].astype(int)
        if gmap:
            cls = np.array([gmap.get(c, -1) for c in cls], int)
        cx, cy, bw, bh = rows[:, 1] * w, rows[:, 2] * h, rows[:, 3] * w, rows[:, 4] * h
        gt = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        for c in want:
            g = gt[cls == c]
            n_gt[c] += len(g)
            idx = np.flatnonzero(dets.cls == c)
            idx = idx[np.argsort(-dets.conf[idx])]
            hits = np.zeros((len(idx), len(IOUS)), bool)
            if len(g) and len(idx):
                iou = iou_matrix(dets.xyxy[idx], g)
                for t, thr in enumerate(IOUS):
                    hits[:, t] = match(iou, thr)
            tp[c].append(hits)
            conf[c].append(dets.conf[idx])
        if n % 50 == 0:
            print(f"{n}/{len(images)}", flush=True)

    print(f"\n{args.model}: val {len(images)} картинок, P/R при порогах model.yaml, iou матчинга 0.5, nms_iou {spec.nms_iou}")
    print("| класс | GT | conf | P | R | mAP50 | mAP50-95 |\n|---|---|---|---|---|---|---|")
    m50, m5095 = [], []
    for c in want:
        if not n_gt[c]:
            continue
        hits = np.concatenate(tp[c]) if tp[c] else np.zeros((0, 10), bool)
        cf = np.concatenate(conf[c]) if conf[c] else np.zeros(0)
        aps = [average_precision(hits[:, t], cf, n_gt[c]) for t in range(len(IOUS))]
        keep = cf >= spec.thresholds[c]
        ntp = int(hits[keep, 0].sum())
        p = ntp / max(1, int(keep.sum()))
        r = ntp / n_gt[c]
        m50.append(aps[0])
        m5095.append(float(np.mean(aps)))
        print(f"| {spec.classes[c]} | {n_gt[c]} | {spec.thresholds[c]:.3f} | {p:.3f} | {r:.3f} | {aps[0]:.4f} | {np.mean(aps):.4f} |")
    print(f"| **все** | | | | | **{np.mean(m50):.4f}** | **{np.mean(m5095):.4f}** |")


if __name__ == "__main__":
    main()
