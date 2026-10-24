#!/usr/bin/env python3
# roboflow-экспорт в coco (как на huggingface) -> раскладка yolov8, которую понимает merge.py
#   python coco2yolo.py datasets/hardhats_raw datasets/hardhats
import json
import shutil
import sys
import zipfile
from pathlib import Path

SPLITS = {"train": "train", "valid": "valid", "test": "test"}


def convert(raw: Path, out: Path) -> None:
    names = None
    for split in SPLITS:
        z = raw / f"{split}.zip"
        if not z.exists():
            continue
        tmp = raw / f"_{split}"
        if not tmp.exists():
            with zipfile.ZipFile(z) as zf:
                zf.extractall(tmp)
        ann = next(tmp.rglob("_annotations.coco.json"))
        coco = json.loads(ann.read_text(encoding="utf-8"))
        # у roboflow нулевая категория — имя проекта, настоящие классы дальше
        cats = sorted((c for c in coco["categories"] if c.get("supercategory") != "none"), key=lambda c: c["id"])
        ids = {c["id"]: i for i, c in enumerate(cats)}
        names = names or [c["name"] for c in cats]
        boxes: dict[int, list[str]] = {}
        for a in coco["annotations"]:
            if a["category_id"] in ids:
                boxes.setdefault(a["image_id"], []).append(a)
        (out / split / "images").mkdir(parents=True, exist_ok=True)
        (out / split / "labels").mkdir(parents=True, exist_ok=True)
        for img in coco["images"]:
            w, h = img["width"], img["height"]
            src = ann.parent / img["file_name"]
            if not src.exists():
                continue
            dst = out / split / "images" / Path(img["file_name"]).name
            shutil.move(str(src), dst)
            lines = []
            for a in boxes.get(img["id"], []):
                x, y, bw, bh = a["bbox"]
                if bw > 1 and bh > 1:
                    lines.append(f"{ids[a['category_id']]} {(x + bw / 2) / w:.6f} {(y + bh / 2) / h:.6f} {bw / w:.6f} {bh / h:.6f}")
            (out / split / "labels" / f"{dst.stem}.txt").write_text("\n".join(lines), encoding="utf-8")
        print(f"{split}: {len(coco['images'])} картинок, {sum(len(v) for v in boxes.values())} рамок")
        shutil.rmtree(tmp, ignore_errors=True)
    (out / "data.yaml").write_text("names:\n" + "".join(f"- {n}\n" for n in names) + f"nc: {len(names)}\n",
                                   encoding="utf-8")
    print("классы:", names)


if __name__ == "__main__":
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
