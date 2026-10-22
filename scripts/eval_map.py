#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_settings  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402
from worker.app.postprocess import decode, iou_matrix, preprocess  # noqa: E402

FILES = {"float32": "best.dynamic.onnx", "float16": "best.fp16.onnx"}
BATCH = 8


def average_precision(tp: np.ndarray, conf: np.ndarray, n_gt: int) -> float:
    if n_gt == 0 or len(tp) == 0:
        return 0.0
    order = np.argsort(-conf)
    tp = tp[order]
    tpc, fpc = np.cumsum(tp), np.cumsum(~tp)
    recall = tpc / n_gt
    precision = tpc / (tpc + fpc)
    mrec = np.concatenate(([0.0], recall, [1.0]))
    mpre = np.concatenate(([1.0], precision, [0.0]))
    mpre = np.flip(np.maximum.accumulate(np.flip(mpre)))
    x = np.linspace(0, 1, 101)
    return float(np.trapezoid(np.interp(x, mrec, mpre), x))


def evaluate(session: ort.InferenceSession, spec, images: list[Path], labels_dir: Path) -> tuple[dict[str, float], float]:
    low = dataclasses.replace(spec, thresholds=np.full(spec.nc, 0.001, np.float32))
    name = session.get_inputs()[0].name
    stats = [([], []) for _ in range(spec.nc)]
    n_gt = np.zeros(spec.nc, int)
    spent = 0.0
    for start in range(0, len(images), BATCH):
        chunk = images[start:start + BATCH]
        batch = np.zeros((BATCH, 3, spec.input_size, spec.input_size), np.float32)
        infos, sizes = [], []
        for i, path in enumerate(chunk):
            image = cv2.imread(str(path))
            sizes.append(image.shape[:2])
            infos.append(preprocess(image, spec.input_size, out=batch[i])[1])
        t0 = time.perf_counter()
        outputs = session.run(None, {name: batch})[0]
        spent += time.perf_counter() - t0
        for i, path in enumerate(chunk):
            dets = decode(outputs[i].astype(np.float32), low, infos[i])
            h, w = sizes[i]
            label = labels_dir / (path.stem + ".txt")
            rows = np.loadtxt(label, ndmin=2) if label.exists() and label.stat().st_size else np.zeros((0, 5))
            gt_cls = rows[:, 0].astype(int)
            cx, cy, bw, bh = rows[:, 1] * w, rows[:, 2] * h, rows[:, 3] * w, rows[:, 4] * h
            gt = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
            for c in range(spec.nc):
                g = gt[gt_cls == c]
                n_gt[c] += len(g)
                idx = np.flatnonzero(dets.cls == c)
                idx = idx[np.argsort(-dets.conf[idx])]
                matched = np.zeros(len(idx), bool)
                if len(g) and len(idx):
                    iou = iou_matrix(dets.xyxy[idx], g)
                    used = set()
                    for k in range(len(idx)):
                        j = int(np.argmax(iou[k]))
                        while iou[k, j] >= 0.5 and j in used:
                            iou[k, j] = 0
                            j = int(np.argmax(iou[k]))
                        if iou[k, j] >= 0.5:
                            used.add(j)
                            matched[k] = True
                stats[c][0].extend(matched.tolist())
                stats[c][1].extend(dets.conf[idx].tolist())
    ap = {spec.classes[c]: average_precision(np.array(stats[c][0], bool), np.array(stats[c][1]), int(n_gt[c]))
          for c in range(spec.nc) if n_gt[c] > 0}
    return ap, spent / len(images) * 1000


def main() -> None:
    settings = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["ppe", "hazard"])
    ap.add_argument("--limit", type=int, default=0, help="сколько картинок val брать; 0 — все")
    args = ap.parse_args()
    merged = Path(settings.train_runs_dir).parent / "merged"
    specs = load_all_specs(settings.models_dir)
    ort.preload_dlls()

    for model in args.models:
        spec = specs[model]
        images = sorted((merged / model / "images" / "val").iterdir())
        if args.limit:
            images = images[:: max(1, len(images) // args.limit)][:args.limit]
        labels = merged / model / "labels" / "val"
        results = {}
        for kind, file in FILES.items():
            path = spec.onnx_path.with_name(file)
            if not path.exists():
                print(f"{model}: нет {path.name}, пропуск")
                continue
            session = ort.InferenceSession(str(path), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            if "CUDAExecutionProvider" not in session.get_providers():
                raise SystemExit("провайдер CUDA недоступен: замер на процессоре не имеет смысла")
            results[kind] = evaluate(session, spec, images, labels)
        if not results:
            continue
        print(f"\n{model}: val, {len(images)} картинок")
        kinds = list(results)
        print("| класс | " + " | ".join(f"AP50 {k}" for k in kinds) + (" | разница |" if len(kinds) == 2 else " |"))
        print("|---|" + "---|" * (len(kinds) + (len(kinds) == 2)))
        for cls in results[kinds[0]][0]:
            vals = [results[k][0][cls] for k in kinds]
            diff = f" {vals[1] - vals[0]:+.4f} |" if len(kinds) == 2 else ""
            print(f"| {cls} | " + " | ".join(f"{v:.4f}" for v in vals) + " |" + diff)
        means = [float(np.mean(list(results[k][0].values()))) for k in kinds]
        diff = f" {means[1] - means[0]:+.4f} |" if len(kinds) == 2 else ""
        print("| **mAP50** | " + " | ".join(f"**{m:.4f}**" for m in means) + " |" + diff)
        print("инференс, мс на кадр (пачка 8): " + ", ".join(f"{k} {results[k][1]:.2f}" for k in kinds))
        if len(kinds) == 2:
            ok = means[0] - means[1] <= 0.01
            print(f"условие «mAP50 падает не больше чем на 0,01»: {'выполнено' if ok else 'НЕ выполнено'}")


if __name__ == "__main__":
    main()
