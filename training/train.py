#!/usr/bin/env python3
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("name", choices=["ppe", "hazard"])
ap.add_argument("--model", default="yolov8s.pt")
ap.add_argument("--epochs", type=int, default=80)
ap.add_argument("--batch", type=int, default=16)
ap.add_argument("--imgsz", type=int, default=640)
ap.add_argument("--resume", action="store_true")
ap.add_argument("--workers", type=int, default=2)
ap.add_argument("--fraction", type=float, default=1.0, help="доля train для быстрой проверки")
ap.add_argument("--run-name", default=None, help="имя папки прогона, по умолчанию имя модели")


def git_state():
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
        return {"commit": head, "uncommitted_changes": dirty}
    except OSError:
        return None


def write_run_info(save_dir, a, data_yaml):
    import importlib.metadata as md
    import torch
    import yaml
    data = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8"))
    root = Path(data["path"])
    info = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "args": vars(a),
        "versions": {p: md.version(p) for p in ("torch", "ultralytics", "onnx")},
        "python": sys.version.split()[0],
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "git": git_state(),
        "classes": [data["names"][k] for k in sorted(data["names"])],
        "images": {s: len(list((root / "images" / s).glob("*"))) for s in ("train", "val", "test")
                   if (root / "images" / s).exists()},
    }
    (save_dir / "run_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


_epoch = {"current": 0, "logged": set()}


def mark_epoch(trainer):
    _epoch["current"] = trainer.epoch + 1


def per_class_logger(trainer):
    try:
        box = trainer.validator.metrics.box
        names = trainer.validator.names
        path = Path(trainer.save_dir) / "per_class.csv"
        new = not path.exists()
        label = "best" if _epoch["current"] in _epoch["logged"] else _epoch["current"]
        _epoch["logged"].add(_epoch["current"])
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["epoch", "class", "precision", "recall", "mAP50", "mAP50-95"])
            for i, c in enumerate(box.ap_class_index):
                w.writerow([label, names[int(c)], f"{box.p[i]:.4f}", f"{box.r[i]:.4f}",
                            f"{box.ap50[i]:.4f}", f"{box.ap[i]:.4f}"])
    except Exception as e:
        print(f"per_class.csv: не записал эпоху ({e})")


def write_final_metrics(save_dir, metrics, names):
    box = metrics.box
    lines = ["| класс | precision | recall | mAP50 | mAP50-95 |", "|---|---|---|---|---|",
             f"| **все** | {box.mp:.3f} | {box.mr:.3f} | {box.map50:.3f} | {box.map:.3f} |"]
    for i, c in enumerate(box.ap_class_index):
        lines.append(f"| {names[int(c)]} | {box.p[i]:.3f} | {box.r[i]:.3f} | {box.ap50[i]:.3f} | {box.ap[i]:.3f} |")
    (save_dir / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    a = ap.parse_args()
    from ultralytics import YOLO, settings
    settings.update({"tensorboard": True})

    run_name = a.run_name or a.name
    data_yaml = f"merged/{a.name}/data.yaml"
    runs = Path("runs").resolve()
    last = runs / run_name / "weights" / "last.pt"
    if a.resume and last.exists():
        model = YOLO(str(last))
        model.add_callback("on_train_epoch_start", mark_epoch)
        model.add_callback("on_fit_epoch_end", per_class_logger)
        model.train(resume=True)
    else:
        model = YOLO(a.model)
        model.add_callback("on_train_epoch_start", mark_epoch)
        model.add_callback("on_fit_epoch_end", per_class_logger)
        model.add_callback("on_train_start", lambda tr: write_run_info(Path(tr.save_dir), a, data_yaml))
        model.train(
            data=data_yaml,
            epochs=a.epochs, imgsz=a.imgsz, batch=a.batch,
            project=str(runs), name=run_name, exist_ok=True,
            patience=20, workers=a.workers, cache=False, fraction=a.fraction,
            mosaic=1.0, close_mosaic=10, fliplr=0.5,
        )
    save_dir = Path(model.trainer.save_dir)
    print(f"Результаты: {save_dir}")
    best = YOLO(str(save_dir / "weights" / "best.pt"))
    metrics = best.val(data=data_yaml, project=str(runs), name=f"{run_name}_val", exist_ok=True)
    write_final_metrics(save_dir, metrics, best.names)
    best.export(format="onnx", imgsz=a.imgsz, opset=12, simplify=True, dynamic=False)
    (save_dir / "DONE").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
