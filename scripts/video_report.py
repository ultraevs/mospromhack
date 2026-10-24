#!/usr/bin/env python3
# как наши модели видят наши ролики: размер людей и голов, сколько голов размечено каской/без каски,
# что даёт второй проход по головам, ложняки hazard. ответ на «упираемся в видео или в модель»
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import ROOT, get_settings  # noqa: E402
from worker.app.crops import crop_box  # noqa: E402
from worker.app.detector import create_detector  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402
from worker.app.postprocess import Detections, decode, hivis_vests, merge_people, preprocess  # noqa: E402

HEAD = ("helmet", "no_helmet")


def frames(path: Path, n: int) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for k in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, round((k + 0.5) * total / n))
        ok, img = cap.read()
        if ok:
            out.append(img)
    cap.release()
    return out


def infer(det, spec, images: list[np.ndarray], low: float | None = None) -> list[Detections]:
    res = []
    for img in images:
        x, info = preprocess(img, spec.input_size)
        out = det.infer(spec.name, x[None])[0]
        res.append(decode(out, spec if low is None else replace(spec, thresholds=np.full_like(spec.thresholds, low)), info))
    return res


def head_hits(dets: Detections, spec, box) -> list[float]:
    # уверенности каска/без каски, центр которых в верхних 35% рамки человека
    x1, y1, x2, y2 = box
    top = y1 + (y2 - y1) * 0.35
    ids = {spec.class_id(c) for c in HEAD}
    out = []
    for b, c, cl in zip(dets.xyxy, dets.conf, dets.cls):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if cl in ids and x1 <= cx <= x2 and y1 <= cy <= top:
            out.append(float(c))
    return out


def pct(a, q) -> float:
    return round(float(np.percentile(a, q)), 1) if len(a) else 0.0


def analyse(name: str, images, det, specs) -> dict:
    ppe, people, hazard = specs["ppe"], specs["people"], specs.get("hazard")
    h, w = images[0].shape[:2]
    to640 = 640 / max(h, w)
    ppe_d = infer(det, ppe, images)
    ppe_low = infer(det, ppe, images, low=0.1)
    ppl = infer(det, people, images)
    pid = ppe.class_id("person")
    vest = ppe.class_id("vest")
    persons, ppe_found, heads, heads_low, crop_heads, vests, hivis, sizes, confs = 0, 0, 0, 0, 0, 0, 0, [], []
    crop_cases = []
    for img, d, dl, p in zip(images, ppe_d, ppe_low, ppl):
        merged = merge_people(d, ppe, p, people)
        with_vest = hivis_vests(img, merged, ppe)
        boxes = [b for b, c in zip(merged.xyxy, merged.cls) if c == pid]
        own = [b for b, c in zip(d.xyxy, d.cls) if c == pid]
        for b in boxes:
            persons += 1
            ph = (b[3] - b[1]) * to640
            sizes.append(ph)
            if own and max(_iou(b, o) for o in own) > 0.3:
                ppe_found += 1
            hits = head_hits(d, ppe, b)
            heads += bool(hits)
            confs += hits
            heads_low += bool(head_hits(dl, ppe, b))
            vests += any(c == vest and _iou(b, vb) > 0 for vb, c in zip(d.xyxy, d.cls))
            hivis += any(c == vest and _iou(b, vb) > 0 for vb, c in zip(with_vest.xyxy, with_vest.cls))
            cb = crop_box(b, 0.5, 0.15, w, h)
            if cb:
                crop_cases.append((img, cb))
    # второй проход: верх человека отдельно в модель, голова там крупнее
    for img, (x1, y1, x2, y2) in crop_cases[:200]:
        cd = infer(det, ppe, [img[y1:y2, x1:x2]])[0]
        crop_heads += any(ppe.classes[c] in HEAD for c in cd.cls)
    row = {
        "video": name, "frames": len(images), "res": f"{w}x{h}",
        "persons_per_frame": round(persons / len(images), 1),
        "person_h_px640": {"p10": pct(sizes, 10), "p50": pct(sizes, 50), "p90": pct(sizes, 90)},
        "head_px640_p50": round(pct(sizes, 50) * 0.15, 1),  # голова ~15% роста
        "ppe_own_person_recall_vs_coco": round(ppe_found / persons, 2) if persons else None,
        "head_classified": round(heads / persons, 2) if persons else None,
        "head_classified_conf0.1": round(heads_low / persons, 2) if persons else None,
        "head_classified_crop_pass": round(crop_heads / min(len(crop_cases), 200), 2) if crop_cases else None,
        "head_conf_p50": pct(confs, 50),
        "vest_model": round(vests / persons, 2) if persons else None,
        "vest_with_hivis": round(hivis / persons, 2) if persons else None,
    }
    if hazard:
        hz = infer(det, hazard, images)
        row["hazard_per_frame"] = {c: round(sum(int((d.cls == hazard.class_id(c)).sum()) for d in hz) / len(images), 2)
                                   for c in hazard.classes}
    return row


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="+", default=["css_test.mp4", "solo-worker.mp4", "factory_floor.mp4", "warehouse.mp4",
                                                     "ptz_yard.mp4", "fall_test.mp4", "fire-explosion.mp4"])
    ap.add_argument("--frames", type=int, default=40)
    args = ap.parse_args()
    settings = get_settings()
    specs = load_all_specs(settings.models_dir)
    det = create_detector("onnx", specs, precision="fp32")
    rows = []
    for v in args.videos:
        images = frames(ROOT / "data" / "videos" / v, args.frames)
        rows.append(analyse(v, images, det, specs))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    out = ROOT / "data" / "video_report.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"отчет: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
