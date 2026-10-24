#!/usr/bin/env python3
import sys
import time
from pathlib import Path

import numpy as np
import yaml
from ultralytics import YOLO

RUNS = Path("runs").resolve()
RECALL_TARGETS = {"ppe": {"helmet": 0.75, "mask": 0.75}}
FIXED = {"ppe": {"vest": (0.30, "вручную: recall 0.80-0.85 на css; на sh17 цель недостижима")}}
REPORT_RECALLS = (0.70, 0.75, 0.80)


def run_val(model, data, tag, split="val"):
    return model.val(data=str(data), split=split, project=str(RUNS), name=tag, exist_ok=True,
                     plots=False, verbose=False, workers=2)


def table(metrics, names):
    box = metrics.box
    rows = ["| класс | рамок | precision | recall | mAP50 | mAP50-95 |", "|---|---|---|---|---|---|",
            f"| **все** | {int(metrics.nt_per_class.sum())} | {box.mp:.3f} | {box.mr:.3f} | {box.map50:.3f} | {box.map:.3f} |"]
    for i, c in enumerate(box.ap_class_index):
        rows.append(f"| {names[int(c)]} | {int(metrics.nt_per_class[int(c)])} | {box.p[i]:.3f} | {box.r[i]:.3f} "
                    f"| {box.ap50[i]:.3f} | {box.ap[i]:.3f} |")
    return "\n".join(rows)


def subset_yaml(data_yaml, source):
    data = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8"))
    root = Path(data["path"])
    imgs = sorted((root / "images" / "val").glob(f"{source}_*"))
    lst = root / f"val_{source}.txt"
    lst.write_text("\n".join(str(p) for p in imgs) + "\n", encoding="utf-8")
    out = root / f"data_val_{source}.yaml"
    out.write_text(yaml.safe_dump({**data, "val": lst.name}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out, len(imgs)


def curves(metrics, names):
    box = metrics.box
    return {names[int(c)]: (np.asarray(box.px), box.p_curve[i], box.r_curve[i], box.f1_curve[i])
            for i, c in enumerate(box.ap_class_index)}


def conf_for_recall(px, p, r, target):
    ok = np.where(r >= target)[0]
    if len(ok) == 0:
        return None
    i = ok.max()
    return float(px[i]), float(p[i]), float(r[i])


def thresholds(name, metrics, names):
    cur = curves(metrics, names)
    targets = RECALL_TARGETS.get(name, {})
    chosen, lines = {}, [f"# Пороги уверенности: {name}", "",
                         "Посчитано на val при IoU 0,5. Выбранный порог записан в model.yaml.", ""]
    for cls, (px, p, r, f1) in cur.items():
        i = int(f1.argmax())
        lines += [f"## {cls}", "", "| точка | порог | precision | recall |", "|---|---|---|---|",
                  f"| максимум F1 | {px[i]:.3f} | {p[i]:.3f} | {r[i]:.3f} |"]
        for t in REPORT_RECALLS:
            hit = conf_for_recall(px, p, r, t)
            lines.append(f"| recall ≥ {t:.2f} | {hit[0]:.3f} | {hit[1]:.3f} | {hit[2]:.3f} |" if hit
                         else f"| recall ≥ {t:.2f} | недостижим | — | максимум {float(r.max()):.3f} |")
        if cls in FIXED.get(name, {}):
            c, why = FIXED[name][cls]
            j = int(np.abs(px - c).argmin())
            chosen[cls] = {"conf": c, "precision": round(float(p[j]), 3), "recall": round(float(r[j]), 3), "rule": why}
            lines.append(f"| порог {c:.2f} | {c:.3f} | {p[j]:.3f} | {r[j]:.3f} |")
        elif cls in targets and conf_for_recall(px, p, r, targets[cls]):
            c, pp, rr = conf_for_recall(px, p, r, targets[cls])
            chosen[cls] = {"conf": round(c, 3), "precision": round(pp, 3), "recall": round(rr, 3),
                           "rule": f"recall >= {targets[cls]}"}
        else:
            chosen[cls] = {"conf": round(float(px[i]), 3), "precision": round(float(p[i]), 3),
                           "recall": round(float(r[i]), 3), "rule": "max F1"}
        lines += ["", f"Выбрано: порог {chosen[cls]['conf']} ({chosen[cls]['rule']}).", ""]
    (RUNS / name / "thresholds.md").write_text("\n".join(lines), encoding="utf-8")
    return chosen


def write_model_yaml(name, model, chosen):
    names = [model.names[i] for i in sorted(model.names)]
    cfg = {
        "model": name,
        "weights": {"onnx": "weights/best.onnx", "pt": "weights/best.pt"},
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "input": {"name": "images", "shape": [1, 3, 640, 640], "layout": "NCHW", "color": "RGB",
                  "scale": "0..1", "letterbox": True},
        "output": {"name": "output0", "shape": [1, 4 + len(names), 8400],
                   "format": "cx, cy, w, h в пикселях входа, затем score по классам; NMS не включён"},
        "nms_iou": 0.7,
        "classes": names,
        "thresholds": {c: chosen[c]["conf"] for c in names if c in chosen},
        "thresholds_info": {c: chosen[c] for c in names if c in chosen},
    }
    (RUNS / name / "model.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


if __name__ == "__main__":
    for name in sys.argv[1:] or ("ppe", "hazard"):
        model = YOLO(str(RUNS / name / "weights" / "best.pt"))
        data = Path("merged") / name / "data.yaml"
        full = run_val(model, data, f"{name}_eval_val")
        chosen = thresholds(name, full, model.names)
        write_model_yaml(name, model, chosen)

        if name == "ppe":
            parts = [f"# ppe: метрики по источникам\n\nЛучшие веса, {time.strftime('%Y-%m-%d')}.\n",
                     f"## Вся валидация\n\n{table(full, model.names)}\n"]
            for src in ("css", "sh17"):
                y, n = subset_yaml(data, src)
                m = run_val(model, y, f"ppe_eval_val_{src}")
                parts.append(f"## val, только {src} ({n} картинок)\n\n{table(m, model.names)}\n")
            t = run_val(model, data, "ppe_eval_test", split="test")
            parts.append(f"## test, только css\n\n{table(t, model.names)}\n")
            (RUNS / "ppe" / "metrics_by_source.md").write_text("\n".join(parts), encoding="utf-8")
    print("Готово: runs/<модель>/model.yaml, thresholds.md, runs/ppe/metrics_by_source.md")
