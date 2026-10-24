#!/usr/bin/env python3
# эталон «каска / без каски» на наших роликах, раз своего теста орги не дают.
#   make  — кадры, люди (coco), листы с номерами в data/heads_gt/sheet_<ролик>.jpg + items.json
#   eval  — после разметки labels.txt: вердикт моделей по каждому человеку против эталона
# labels.txt: строка на человека «<номер> h|n|?»  (h — каска, n — без каски, ? — не разобрать)
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
from worker.app.detector import create_detector  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402
from worker.app.postprocess import Detections, decode, merge_heads, merge_people, preprocess  # noqa: E402

OUT = ROOT / "data" / "heads_gt"
GT = ROOT / "eval" / "heads_gt"  # эталон лежит в репо, make пишет в data
VIDEOS = ["solo-worker.mp4", "ptz_yard.mp4", "fire-explosion.mp4", "factory_floor.mp4", "warehouse.mp4",
          "fall_test.mp4", "css_test.mp4"]


def read_frame(video: str, idx: int) -> np.ndarray:
    cap = cv2.VideoCapture(str(ROOT / "data" / "videos" / video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, img = cap.read()
    cap.release()
    return img


def run(det, spec, img, low: float | None = None) -> Detections:
    x, info = preprocess(img, spec.input_size)
    out = det.infer(spec.name, x[None])[0]
    return decode(out, spec if low is None else replace(spec, thresholds=np.full_like(spec.thresholds, low)), info)


def tiled(fn, img) -> Detections:
    # квадратные плитки по высоте кадра, слева и справа: голова на входе модели ×W/H крупнее
    h, w = img.shape[:2]
    if w <= h * 1.2:
        return fn(img)
    parts = []
    for x in (0, w - h):
        d = fn(img[:, x:x + h])
        d.xyxy[:, [0, 2]] += x
        parts.append(d)
    from worker.app.crops import merge
    out = parts[0]
    for d in parts[1:]:
        out = merge(out, d, 0.5)
    return out


def make(args) -> None:
    specs = load_all_specs(get_settings().models_dir)
    det = create_detector("onnx", specs, precision="fp32")
    ppe, people = specs["ppe"], specs["people"]
    OUT.mkdir(parents=True, exist_ok=True)
    items = []
    for video in VIDEOS:
        cap = cv2.VideoCapture(str(ROOT / "data" / "videos" / video))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        tiles = []
        for k in range(args.frames):
            idx = round((k + 0.5) * total / args.frames)
            img = read_frame(video, idx)
            if img is None:
                continue
            p = run(det, people, img)
            for b, c, cf in zip(p.xyxy, p.cls, p.conf):
                if people.classes[c] != "person" or cf < 0.5:
                    continue
                x1, y1, x2, y2 = map(int, b)
                h = y2 - y1
                if h < 24:
                    continue
                crop = img[max(0, y1 - h // 8): y1 + int(h * 0.5), max(0, x1 - h // 10): x2 + h // 10]
                if crop.size == 0:
                    continue
                n = len(items)
                items.append({"id": n, "video": video, "frame": idx, "bbox": [x1, y1, x2, y2]})
                t = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_CUBIC)
                cv2.rectangle(t, (0, 0), (44, 18), (0, 0, 0), -1)
                cv2.putText(t, str(n), (2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                tiles.append(t)
        if not tiles:
            continue
        while len(tiles) % 8:
            tiles.append(np.zeros_like(tiles[0]))
        grid = np.vstack([np.hstack(tiles[i:i + 8]) for i in range(0, len(tiles), 8)])
        cv2.imwrite(str(OUT / f"sheet_{Path(video).stem}.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(video, sum(1 for it in items if it["video"] == video))
    (OUT / "items.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    print(f"людей {len(items)}; разметка -> {OUT / 'labels.txt'}")


def verdict(dets: Detections, spec, box) -> str | None:
    # лучшая по уверенности голова в верхней трети человека
    x1, y1, x2, y2 = box
    h = y2 - y1
    best = None
    for b, c, cf in zip(dets.xyxy, dets.cls, dets.conf):
        name = spec.classes[c]
        if name not in ("helmet", "no_helmet"):
            continue
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if x1 <= cx <= x2 and y1 - 0.1 * h <= cy <= y1 + 0.35 * h and (best is None or cf > best[0]):
            best = (cf, name)
    return None if best is None else ("h" if best[1] == "helmet" else "n")


def evaluate(args) -> None:
    items = {it["id"]: it for it in json.loads((GT / "items.json").read_text(encoding="utf-8"))}
    gt = {}
    for line in (GT / "labels.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("#")[0].split()
        if len(parts) >= 2 and parts[1] in ("h", "n"):
            gt[int(parts[0])] = parts[1]
    specs = load_all_specs(get_settings().models_dir)
    if args.heads:
        # кандидат ещё не в models/, чтобы воркер его не подхватил до проверки
        from worker.app.model_spec import load_model_spec
        specs["heads"] = load_model_spec(Path(args.heads))
    det = create_detector("onnx", specs, precision="fp32")
    ppe = specs["ppe"]
    variants = {"ppe": lambda img: run(det, ppe, img), "ppe_tiles": lambda img: tiled(lambda im: run(det, ppe, im), img)}
    if "heads" in specs:
        hs = specs["heads"]
        variants["heads"] = lambda img: merge_heads(run(det, ppe, img), ppe, run(det, hs, img), hs)
        variants["heads_tiles"] = lambda img: merge_heads(run(det, ppe, img), ppe,
                                                          tiled(lambda im: run(det, hs, im), img), hs)
    report = {}
    for name, fn in variants.items():
        rows = {"all": [], **{v: [] for v in VIDEOS}}
        cache: dict[tuple, Detections] = {}
        for n, label in gt.items():
            it = items[n]
            key = (it["video"], it["frame"])
            if key not in cache:
                cache[key] = fn(read_frame(*key))
            got = verdict(cache[key], ppe, it["bbox"])
            rows["all"].append((label, got))
            rows[it["video"]].append((label, got))
        report[name] = {k: score(v) for k, v in rows.items() if v}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    (OUT / "eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def score(pairs) -> dict:
    n = len(pairs)
    judged = [(g, p) for g, p in pairs if p is not None]
    correct = sum(g == p for g, p in judged)
    viol = [p for g, p in pairs if g == "n"]
    false_viol = sum(1 for g, p in pairs if g == "h" and p == "n")
    return {"people": n, "judged": round(len(judged) / n, 2), "correct_of_judged": round(correct / len(judged), 2)
            if judged else None, "correct_of_all": round(correct / n, 2),
            "violations_caught": f"{sum(p == 'n' for p in viol)}/{len(viol)}",
            "false_violations": f"{false_viol}/{sum(1 for g, _ in pairs if g == 'h')}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make")
    p.add_argument("--frames", type=int, default=6)
    p = sub.add_parser("eval")
    p.add_argument("--heads", help="папка кандидата модели голов (best.onnx + model.yaml) вне models/")
    args = ap.parse_args()
    make(args) if args.cmd == "make" else evaluate(args)


if __name__ == "__main__":
    main()
