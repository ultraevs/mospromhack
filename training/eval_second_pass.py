#!/usr/bin/env python3
from pathlib import Path

import cv2
import torch
import yaml
from torchvision.ops import nms
from ultralytics import YOLO

ROOT = Path("merged/ppe")
cfg = yaml.safe_load(Path("runs/ppe/model.yaml").read_text(encoding="utf-8"))
NAMES, THR = cfg["classes"], cfg["thresholds"]
CID = {n: i for i, n in enumerate(NAMES)}
TARGETS = ("no_helmet", "no_mask")
MIN_CONF = 0.05
MERGE_IOU = 0.5
MATCH_IOU = 0.5
TOP, MARGIN = 0.5, 0.15


def iou(a, b):
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def load(split):
    items = []
    for img in sorted((ROOT / "images" / split).glob("css_*")):
        im = cv2.imread(str(img))
        h, w = im.shape[:2]
        gt = []
        for l in (ROOT / "labels" / split / f"{img.stem}.txt").read_text().splitlines():
            c, cx, cy, bw, bh = l.split()
            cx, cy, bw, bh = float(cx) * w, float(cy) * h, float(bw) * w, float(bh) * h
            gt.append((int(c), (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)))
        items.append((im, gt))
    return items


def boxes_of(result, dx=0.0, dy=0.0):
    b = result.boxes
    return [(int(c), float(s), (x1 + dx, y1 + dy, x2 + dx, y2 + dy))
            for (x1, y1, x2, y2), s, c in zip(b.xyxy.cpu().tolist(), b.conf.cpu().tolist(), b.cls.cpu().tolist())]


def run(model, items):
    frame, crops, n_crops = [], [], 0
    for im, _ in items:
        first = boxes_of(model.predict(im, conf=MIN_CONF, iou=cfg["nms_iou"], imgsz=640, verbose=False)[0])
        frame.append(first)
        H, W = im.shape[:2]
        extra = []
        for c, s, (x1, y1, x2, y2) in first:
            if c != CID["person"] or s < THR["person"]:
                continue
            w, h = x2 - x1, (y2 - y1) * TOP
            cx1, cy1 = max(0, int(x1 - w * MARGIN)), max(0, int(y1 - h * MARGIN))
            cx2, cy2 = min(W, int(x2 + w * MARGIN)), min(H, int(y1 + h + h * MARGIN))
            if cx2 - cx1 < 8 or cy2 - cy1 < 8:
                continue
            n_crops += 1
            r = model.predict(im[cy1:cy2, cx1:cx2], conf=MIN_CONF, iou=cfg["nms_iou"], imgsz=640, verbose=False)[0]
            extra += boxes_of(r, cx1, cy1)
        crops.append(extra)
    return frame, crops, n_crops


def merged(first, extra, cls):
    cand = [(s, b) for c, s, b in first + extra if c == cls]
    if not cand:
        return []
    keep = nms(torch.tensor([b for _, b in cand]), torch.tensor([s for s, _ in cand]), MERGE_IOU)
    return [cand[i] for i in keep.tolist()]


def score(items, preds_per_image, cls, thr):
    tp = n_pred = n_gt = 0
    for (_, gt), preds in zip(items, preds_per_image):
        g = [b for c, b in gt if c == cls]
        p = sorted((x for x in preds if x[0] >= thr), key=lambda x: -x[0])
        n_gt += len(g)
        n_pred += len(p)
        used = set()
        for _, pb in p:
            best, bi = -1, MATCH_IOU
            for k, gb in enumerate(g):
                if k not in used and iou(pb, gb) >= bi:
                    best, bi = k, iou(pb, gb)
            if best >= 0:
                used.add(best)
                tp += 1
    return tp / max(n_pred, 1), tp / max(n_gt, 1), n_pred, n_gt


if __name__ == "__main__":
    model = YOLO("runs/ppe/weights/best.pt")
    out = ["| набор | класс | вариант | порог | precision | recall | предсказаний | эталонов |",
           "|---|---|---|---|---|---|---|---|"]
    for split in ("val", "test"):
        items = load(split)
        frame, crops, n_crops = run(model, items)
        print(f"css {split}: {len(items)} картинок, вырезок {n_crops} ({n_crops / len(items):.1f} на картинку)")
        for name in TARGETS:
            c = CID[name]
            only = [[(s, b) for cc, s, b in f if cc == c] for f in frame]
            both = [merged(f, e, c) for f, e in zip(frame, crops)]
            rows = [("общий кадр", only, THR[name]), ("общий кадр + вырезки", both, THR[name]),
                    ("общий кадр", only, 0.25), ("общий кадр + вырезки", both, 0.25),
                    ("общий кадр", only, 0.15), ("общий кадр + вырезки", both, 0.15)]
            for label, preds, thr in rows:
                p, r, n_pred, n_gt = score(items, preds, c, thr)
                out.append(f"| css {split} | {name} | {label} | {thr:.3f} | {p:.2f} | {r:.2f} | {n_pred} | {n_gt} |")
    print("\n".join(out))
