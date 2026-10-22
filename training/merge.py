#!/usr/bin/env python3
import argparse
import hashlib
import os
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

import yaml

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLIT_ALIASES = {
    "train": "train", "training": "train",
    "valid": "val", "val": "val", "validation": "val",
    "test": "test",
}


def norm(name):
    return str(name).strip().lower().replace("_", "-").replace(" ", "-")


def load_names(src):
    root = Path(src["path"])
    for y in sorted(root.rglob("*.y*ml"), key=lambda p: len(p.parts)):
        try:
            data = yaml.safe_load(y.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and "names" in data:
            names = data["names"]
            if isinstance(names, dict):
                names = [names[k] for k in sorted(names, key=int)]
            return list(names), str(y)
    if "names" in src:
        return list(src["names"]), "конфиг"
    sys.exit(f"[{src['name']}] не нашёл data.yaml с names в {root} "
             f"и в конфиге нет поля names")


def find_label(img):
    parts = list(img.parts)
    for i in range(len(parts) - 2, -1, -1):
        if parts[i].lower() == "images":
            cand = Path(*parts[:i], "labels", *parts[i + 1:]).with_suffix(".txt")
            if cand.exists():
                return cand
            break
    cand = img.with_suffix(".txt")
    return cand if cand.exists() else None


def read_boxes(label_path):
    boxes = []
    for line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        tok = line.split()
        if len(tok) < 5:
            continue
        try:
            cls = int(float(tok[0]))
            vals = [float(v) for v in tok[1:]]
        except ValueError:
            continue
        if len(vals) == 4:
            cx, cy, w, h = vals
        elif len(vals) >= 6 and len(vals) % 2 == 0:
            xs, ys = vals[0::2], vals[1::2]
            x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
            cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1
        else:
            continue
        x1, y1 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
        x2, y2 = min(1.0, cx + w / 2), min(1.0, cy + h / 2)
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            continue
        boxes.append((cls, (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1))
    return boxes


def overlap(a, b):
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1, bx2, by2 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw, ih = min(ax2, bx2) - max(ax1, bx1), min(ay2, by2) - max(ay1, by1)
    if iw <= 0 or ih <= 0:
        return 0.0
    return iw * ih / min(a[2] * a[3], b[2] * b[3])


def stable_fraction(key):
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def load_split_lists(src):
    result = {}
    for split, fname in (src.get("split_files") or {}).items():
        p = Path(src["path"]) / fname
        if not p.exists():
            hits = list(Path(src["path"]).rglob(Path(fname).name))
            p = hits[0] if hits else p
        if not p.exists():
            print(f"  ! список сплита {fname} не найден, делю по хэшу")
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            name = line.strip().replace("\\", "/").split("/")[-1]
            if name:
                result[Path(name).stem] = split
    return result


def pick_split(img, root, split_lists, val_fraction):
    if img.stem in split_lists:
        return split_lists[img.stem]
    for part in img.relative_to(root).parts[:-1]:
        if part.lower() in SPLIT_ALIASES:
            return SPLIT_ALIASES[part.lower()]
    return "val" if stable_fraction(img.name) < val_fraction else "train"


def place(src_file, dst_file):
    if dst_file.exists():
        dst_file.unlink()
    try:
        os.link(src_file, dst_file)
    except OSError:
        shutil.copy2(src_file, dst_file)


def process_source(src, classes, out, val_fraction, stats):
    root = Path(src["path"])
    if not root.exists():
        print(f"\n[{src['name']}] ПРОПУСК: нет папки {root}")
        return
    names, names_from = load_names(src)
    target_id = {c: i for i, c in enumerate(classes)}
    mapping = {norm(k): v for k, v in (src.get("map") or {}).items()}
    for v in mapping.values():
        if v not in target_id:
            sys.exit(f"[{src['name']}] в map указан класс '{v}', которого нет в classes")

    print(f"\n[{src['name']}] {root}  (имена классов: {names_from})")
    for i, n in enumerate(names):
        print(f"    {i:>2} {n:<22} -> {mapping.get(norm(n), '— выброшен')}")
    known = {norm(n) for n in names}
    for k in mapping:
        if k not in known:
            print(f"  ! в map есть '{k}', но такого класса в источнике нет")

    rules = []
    for r in src.get("derive") or []:
        unless = r.get("unless_overlaps") or []
        unless = [unless] if isinstance(unless, str) else unless
        if r["to"] not in target_id:
            sys.exit(f"[{src['name']}] derive -> '{r['to']}' нет в classes")
        rules.append((norm(r["from"]), {norm(u) for u in unless}, r["to"],
                      float(r.get("min_overlap", 0.15)),
                      float(r.get("keep_top", 1.0)), float(r.get("keep_bottom", 1.0))))
        crop = (f", верхние {r['keep_top']:.0%} рамки" if "keep_top" in r else "") + \
               (f", нижние {r['keep_bottom']:.0%} рамки" if "keep_bottom" in r else "")
        print(f"    правило: {r['from']} без {unless} -> {r['to']}{crop}")

    split_lists = load_split_lists(src)
    images = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMG_EXT)
    limit = src.get("max_images")
    if limit and len(images) > limit:
        images = sorted(images, key=lambda p: stable_fraction(p.name))[:limit]
        print(f"    взято {limit} картинок из-за max_images")
    keep_empty = src.get("keep_empty", True)

    n_img = 0
    for img in images:
        label = find_label(img)
        raw = read_boxes(label) if label else []
        named = [(norm(names[c]) if 0 <= c < len(names) else None, b)
                 for c, *b in raw]
        out_boxes = []
        for name, b in named:
            if name in mapping:
                out_boxes.append((target_id[mapping[name]], b))
        for frm, unless, to, thr, top, bottom in rules:
            blockers = [b for name, b in named if name in unless]
            for name, b in named:
                if name == frm and not any(overlap(b, o) >= thr for o in blockers):
                    cx, cy, w, h = b
                    if top < 1.0:
                        b = [cx, cy - h / 2 + h * top / 2, w, h * top]
                    elif bottom < 1.0:
                        b = [cx, cy + h / 2 - h * bottom / 2, w, h * bottom]
                    out_boxes.append((target_id[to], b))
        if not out_boxes and not keep_empty:
            continue

        split = pick_split(img, root, split_lists, val_fraction)
        stem = f"{src['name']}_{img.stem}"
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        place(img, out / "images" / split / f"{stem}{img.suffix.lower()}")
        (out / "labels" / split / f"{stem}.txt").write_text(
            "".join(f"{c} {b[0]:.6f} {b[1]:.6f} {b[2]:.6f} {b[3]:.6f}\n"
                    for c, b in out_boxes), encoding="utf-8")
        n_img += 1
        stats["images"][split] += 1
        if not out_boxes:
            stats["empty"][split] += 1
        for c, _ in out_boxes:
            stats["boxes"][(split, c)] += 1
    print(f"    картинок добавлено: {n_img}")


def make_preview(out, classes, count):
    try:
        import cv2
    except ImportError:
        print("\nПревью пропущено: нет opencv (pip install opencv-python)")
        return
    files = sorted((out / "images" / "train").glob("*"))
    random.Random(0).shuffle(files)
    prev = out / "preview"
    prev.mkdir(exist_ok=True)
    palette = [(66, 135, 245), (60, 200, 80), (40, 40, 230), (230, 180, 40),
               (200, 60, 200), (40, 200, 220), (120, 120, 120), (20, 140, 255)]
    done = 0
    for f in files:
        if done >= count:
            break
        img = cv2.imread(str(f))
        if img is None:
            continue
        h, w = img.shape[:2]
        lbl = out / "labels" / "train" / f"{f.stem}.txt"
        for line in lbl.read_text().splitlines():
            c, cx, cy, bw, bh = line.split()
            c = int(c)
            x1, y1 = int((float(cx) - float(bw) / 2) * w), int((float(cy) - float(bh) / 2) * h)
            x2, y2 = int((float(cx) + float(bw) / 2) * w), int((float(cy) + float(bh) / 2) * h)
            col = palette[c % len(palette)]
            cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
            cv2.putText(img, classes[c], (x1, max(12, y1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
        cv2.imwrite(str(prev / f"{f.stem}.jpg"), img)
        done += 1
    print(f"\nПревью: {done} картинок с рамками в {prev}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("config")
    ap.add_argument("--preview", type=int, default=0,
                    help="сколько картинок с нарисованными рамками сохранить для проверки глазами")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    classes = list(cfg["classes"])
    out = Path(cfg["output"])
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    stats = {"images": Counter(), "empty": Counter(), "boxes": Counter()}
    for src in cfg["sources"]:
        process_source(src, classes, out, float(cfg.get("val_fraction", 0.1)), stats)

    if not stats["images"]:
        sys.exit("\nНи одной картинки не собрано. Проверь пути в конфиге.")

    splits = [s for s in ("train", "val", "test") if stats["images"][s]]
    data = {"path": str(out.resolve()), "train": "images/train",
            "val": "images/val" if "val" in splits else "images/train"}
    if "test" in splits:
        data["test"] = "images/test"
    data["names"] = {i: c for i, c in enumerate(classes)}
    (out / "data.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"{'':<14}" + "".join(f"{s:>10}" for s in splits))
    print(f"{'картинок':<14}" + "".join(f"{stats['images'][s]:>10}" for s in splits))
    print(f"{'из них пустых':<14}" + "".join(f"{stats['empty'][s]:>10}" for s in splits))
    print("-" * 60)
    for i, c in enumerate(classes):
        row = "".join(f"{stats['boxes'][(s, i)]:>10}" for s in splits)
        warn = "   <- НЕТ ДАННЫХ" if not stats["boxes"][("train", i)] else ""
        print(f"{c:<14}{row}{warn}")
    print("=" * 60)
    print(f"Готово: {out / 'data.yaml'}")

    if args.preview:
        make_preview(out, classes, args.preview)


if __name__ == "__main__":
    main()
