#!/usr/bin/env python3
# метрики для жюри: P/R/F1 при рабочем пороге, mAP50, mAP50-95, float32 (onnx) и int8 (симуляция sdk = tpu)
# пороги: float из model.yaml, int8 из linq/meta.yaml (если там нет — как в воркере, порог float)
# полный val в float: --limit 0 --float-only --boot 1000 --out data/linq/metrics_full.json
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
from worker.app.model_spec import load_model_spec  # noqa: E402
from worker.app.postprocess import decode, iou_matrix, preprocess  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IOUS = np.linspace(0.5, 0.95, 10)


# как match из eval_map, но совпадение сразу на 10 порогах iou: tp shape (n_det, 10)
# groups: имя -> (классы модели, id в разметке); несколько классов модели = сведены в один (car+bus -> vehicle)
# возвращает по картинкам {имя: (tp, conf, n_gt)} — бутстрэп потом пересчитывает без инференса
def match_iou(session, spec, images, labels_dir, groups, tag=""):
    low = dataclasses.replace(spec, thresholds=np.full(spec.nc, 0.001, np.float32))
    name = session.get_inputs()[0].name
    per_img = []
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
            img = {}
            for gname, (mcls, lc) in groups.items():
                g = gt[gt_cls == lc]
                idx = np.flatnonzero(np.isin(dets.cls, mcls))
                idx = idx[np.argsort(-dets.conf[idx])]
                if len(mcls) > 1 and len(idx) > 1:
                    # одна машина бывает и car и truck — давим дубли между классами (iou 0.7)
                    iou = iou_matrix(dets.xyxy[idx], dets.xyxy[idx])
                    keep = np.ones(len(idx), bool)
                    for k in range(len(idx)):
                        if keep[k]:
                            keep[k + 1:] &= iou[k, k + 1:] < 0.7
                    idx = idx[keep]
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
                img[gname] = (tp, dets.conf[idx].astype(np.float32), len(g))
            per_img.append(img)
        done = start + len(chunk)
        if done % 200 < BATCH or done == len(images):
            print(f"  {tag} {done}/{len(images)} ({time.time() - t0:.0f} с)", flush=True)
    return per_img


def gather(per_img, gname, sel=None):
    sel = range(len(per_img)) if sel is None else sel
    tp = np.concatenate([per_img[i][gname][0] for i in sel])
    conf = np.concatenate([per_img[i][gname][1] for i in sel])
    return tp, conf, int(sum(per_img[i][gname][2] for i in sel))


def summarize(tp, conf, n_gt, thr):
    ap = [average_precision(tp[:, t], conf, n_gt) for t in range(len(IOUS))]
    keep = conf >= thr
    hit = int(tp[keep, 0].sum())
    p = hit / max(int(keep.sum()), 1)
    r = hit / max(n_gt, 1)
    f1 = 2 * p * r / max(p + r, 1e-9)
    return {"thr": round(float(thr), 3), "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3),
            "map50": round(ap[0], 3), "map50_95": round(float(np.mean(ap)), 3), "boxes": int(n_gt)}


def ap50_pr(tp, conf, n_gt, thr):
    keep = conf >= thr
    hit = int(tp[keep, 0].sum())
    return average_precision(tp[:, 0], conf, n_gt), hit / max(int(keep.sum()), 1), hit / max(n_gt, 1)


# 95% ДИ: ресэмпл картинок с возвращением, пересчёт AP50 и P/R по классам и среднего mAP50
def bootstrap(per_img, thrs, n, seed=0):
    rng = np.random.default_rng(seed)
    vals = {k: [] for k in thrs}
    means = []
    for _ in range(n):
        sel = rng.integers(0, len(per_img), len(per_img))
        aps = []
        for k, thr in thrs.items():
            v = ap50_pr(*gather(per_img, k, sel), thr)
            vals[k].append(v)
            aps.append(v[0])
        means.append(np.mean(aps))
    ci = lambda a: [round(float(np.percentile(a, 2.5)), 3), round(float(np.percentile(a, 97.5)), 3)]  # noqa: E731
    out = {k: {"map50_ci": ci([v[0] for v in vs]), "precision_ci": ci([v[1] for v in vs]),
               "recall_ci": ci([v[2] for v in vs])} for k, vs in vals.items()}
    return out, ci(means)


class GpuSession:
    # float на cuda если есть, иначе cpu; батч целиком если вход динамический
    def __init__(self, path):
        import onnxruntime as ort
        prov = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
        self.s = ort.InferenceSession(str(path), providers=prov)
        self.name = self.s.get_inputs()[0].name
        self.batched = self.s.get_inputs()[0].shape[0] != 1
        print("  providers:", self.s.get_providers(), "батч:", self.batched, flush=True)

    def get_inputs(self):
        return self.s.get_inputs()

    def run(self, _o, feed):
        x = next(iter(feed.values()))
        if self.batched:
            return self.s.run(None, {self.name: x})
        return [np.concatenate([self.s.run(None, {self.name: b[None]})[0] for b in x])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True, type=Path, help="папка с images/val и labels/val")
    ap.add_argument("--limit", type=int, default=150, help="0 = весь val")
    ap.add_argument("--only", nargs="*", default=[], help="класс=id_в_разметке, напр. person=0 vehicle=6")
    ap.add_argument("--merge", nargs="*", default=[], help="свести классы модели: vehicle=car,motorcycle,bus,truck")
    ap.add_argument("--float-only", action="store_true", help="int8 не гонять")
    ap.add_argument("--boot", type=int, default=0, help="ресэмплов бутстрэпа для 95%% ДИ, 0 = без")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "linq" / "metrics.json")
    args = ap.parse_args()

    spec = load_model_spec(ROOT / "models" / args.model)
    linq_dir = ROOT / "models" / args.model / "linq"
    meta = yaml.safe_load((linq_dir / "meta.yaml").read_text(encoding="utf-8"))
    # сортируем по имени с учётом регистра, как в докере (WindowsPath сравнивает без регистра)
    images = sorted((args.data / "images" / "val").iterdir(), key=lambda p: p.name)
    if args.limit:
        images = images[:: max(1, len(images) // args.limit)][:args.limit]
    labels = args.data / "labels" / "val"
    merged = {k: v.split(",") for k, v in (m.split("=") for m in args.merge)}
    if args.only:
        groups = {}
        for o in args.only:
            n, lc = o.split("=")
            groups[n] = ([spec.class_id(x) for x in merged.get(n, [n])], int(lc))
    else:
        groups = {spec.classes[c]: ([c], c) for c in range(spec.nc)}
    int8_thr = meta.get("thresholds") or {}

    result = {"data": str(args.data), "images": len(images), "iou": "0.5 для P/R/F1 и mAP50, 0.5:0.95 шаг 0.05",
              "float32": {}, "int8": {}}
    if merged:
        result["merge"] = {k: ",".join(v) for k, v in merged.items()}
    kinds = ["float32"] if args.float_only else ["float32", "int8"]
    t0 = time.time()
    for kind in kinds:
        if kind == "float32":
            session = GpuSession(spec.onnx_path)
        else:
            from scripts.linq_eval import QuantSession  # sdk linq только в докере
            session = QuantSession(linq_dir, meta)
        per_img = match_iou(session, spec, images, labels, groups, f"{args.model} {kind}")
        thrs = {}
        for gname, (mcls, _) in groups.items():
            tp, conf, ng = gather(per_img, gname)
            if ng == 0:
                continue
            # сведённый класс — минимальный порог из составляющих
            if kind == "float32":
                thrs[gname] = min(float(spec.thresholds[c]) for c in mcls)
            else:
                thrs[gname] = min(float(int8_thr.get(spec.classes[c], spec.thresholds[c])) for c in mcls)
            result[kind][gname] = summarize(tp, conf, ng, thrs[gname])
        if args.boot:
            tb = time.time()
            ci, mean_ci = bootstrap(per_img, thrs, args.boot)
            for k, v in ci.items():
                result[kind][k].update(v)
            result[kind + "_map50_ci"] = mean_ci
            print(f"  бутстрэп {args.boot}: {time.time() - tb:.0f} с", flush=True)
    result["seconds"] = round(time.time() - t0)
    if args.boot:
        result["boot"] = args.boot

    print(f"\n{args.model}: {len(images)} картинок val ({args.data})")
    print("| класс | рамок | порог | P | R | F1 | mAP50 [95% ДИ] | mAP50-95 |")
    print("|---|---|---|---|---|---|---|---|")
    for kind in kinds:
        for name, f in result[kind].items():
            ci = f" [{f['map50_ci'][0]:.3f}–{f['map50_ci'][1]:.3f}]" if "map50_ci" in f else ""
            print(f"| {name} {kind} | {f['boxes']} | {f['thr']:.3f} | {f['precision']:.3f} | {f['recall']:.3f} "
                  f"| {f['f1']:.3f} | {f['map50']:.3f}{ci} | {f['map50_95']:.3f} |")
        vals = result[kind].values()
        result[kind + "_mean"] = {k: round(float(np.mean([v[k] for v in vals])), 3) for k in ("map50", "map50_95")}
        print(f"среднее {kind} {result[kind + '_mean']} ДИ mAP50 {result.get(kind + '_map50_ci')}")
    print(f"время {result['seconds']} с")

    allres = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    allres[args.model] = result
    args.out.write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
