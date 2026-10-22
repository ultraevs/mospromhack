#!/usr/bin/env python3
import os
import sys
from pathlib import Path

ROOT = Path("datasets")
# ключи: $env:ROBOFLOW_API_KEY и ~/.kaggle/kaggle.json
ROBOFLOW = {
    "css": ("roboflow-universe-projects", "construction-site-safety", 27),  # v27 а не v30, в v30 всего 717 картинок
    "smoker": ("cigaretteple-7m0hn", "smoker-yolo", 2),
}
KAGGLE = {
    "sh17": "mugheesahmad/sh17-dataset-for-ppe-detection",
    "dfire": "sayedgamal99/smoke-fire-detection-yolo",
}


def roboflow_get(name):
    from roboflow import Roboflow
    key = os.environ.get("ROBOFLOW_API_KEY") or sys.exit("нет ROBOFLOW_API_KEY")
    ws, proj, ver = ROBOFLOW[name]
    project = Roboflow(api_key=key).workspace(ws).project(proj)
    if ver is None:
        ver = max(int(v.version) for v in project.versions())
    project.version(ver).download("yolov8", location=str(ROOT / name))


MAX_SIDE = 1280
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _extract_chunk(zip_path, dest, names):
    import zipfile
    import cv2
    import numpy as np
    with zipfile.ZipFile(zip_path) as zf:
        for name in names:
            out = dest / name
            out.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(name)
            if out.suffix.lower() in IMG_EXT:
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    h, w = img.shape[:2]
                    s = MAX_SIDE / max(h, w)
                    if s < 1:
                        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
                    cv2.imwrite(str(out.with_suffix(".jpg")), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                    continue
            out.write_bytes(data)
    return len(names)


def extract_resized(zip_path, dest):
    import zipfile
    from concurrent.futures import ProcessPoolExecutor
    with zipfile.ZipFile(zip_path) as zf:
        members = [m.filename for m in zf.infolist() if not m.is_dir()]
    chunks = [members[i::64] for i in range(64)]
    done = 0
    with ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 1)) as ex:
        for n in ex.map(_extract_chunk, [zip_path] * len(chunks), [dest] * len(chunks), chunks):
            done += n
            print(f"  распаковано {done}/{len(members)}")


def kaggle_get(name):
    from kaggle.api.kaggle_api_extended import KaggleApi
    api = KaggleApi()
    api.authenticate()
    dest = ROOT / name
    dest.mkdir(parents=True, exist_ok=True)
    if not any(dest.glob("*.zip")):
        api.dataset_download_files(KAGGLE[name], path=str(dest), unzip=False, quiet=False)
    for z in dest.glob("*.zip"):
        print(f"  распаковываю {z.name} с ужатием до {MAX_SIDE}px...")
        extract_resized(z, dest)
        z.unlink()


if __name__ == "__main__":
    wanted = sys.argv[1:] or [*ROBOFLOW, *KAGGLE]
    for name in wanted:
        d = ROOT / name
        if d.exists() and any(d.iterdir()) and not any(d.glob("*.zip")):
            print(f"[{name}] уже есть, пропускаю")
            continue
        print(f"[{name}] качаю...")
        (roboflow_get if name in ROBOFLOW else kaggle_get)(name)
    print("Готово. Дальше: python merge.py configs/ppe.yaml --preview 40")
