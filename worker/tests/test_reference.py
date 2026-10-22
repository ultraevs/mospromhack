from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from shared.config import get_settings
from worker.app.postprocess import decode, iou_matrix, preprocess

pytestmark = [pytest.mark.reference, pytest.mark.models]

N_IMAGES = 20
PAIR_IOU = 0.9
NEAR_THRESHOLD = 0.02  # расхождения только у рамок около порога
MAX_BORDERLINE = 3


def train_dir() -> Path:
    runs = get_settings().train_runs_dir
    if not runs or not Path(runs).exists():
        pytest.skip("TRAIN_RUNS_DIR не задан или не существует")
    return Path(runs)


def pick(folder: Path, pattern: str) -> list[Path]:
    files = sorted(folder.glob(pattern))
    if len(files) < N_IMAGES:
        pytest.skip(f"в {folder} меньше {N_IMAGES} картинок")
    step = len(files) // N_IMAGES
    return files[::step][:N_IMAGES]


def compare(model: str, images: list[Path], real_specs):
    ultralytics = pytest.importorskip("ultralytics")
    from worker.app.detector.onnx_detector import OnnxDetector

    spec = real_specs[model]
    weights = train_dir() / model / "weights" / "best.pt"
    if not weights.exists():
        pytest.skip(f"нет {weights}")

    results = ultralytics.YOLO(str(weights)).predict(
        [str(p) for p in images], conf=float(spec.thresholds.min()), iou=spec.nms_iou, imgsz=spec.input_size,
        # rect=False иначе ultralytics не паддит до 640 как наш onnx
        rect=False, device="cpu", verbose=False)
    detector = OnnxDetector({model: spec}, allow_cuda=False)

    total = borderline = 0
    hard = []
    for path, res in zip(images, results):
        ref_xyxy = res.boxes.xyxy.numpy()
        ref_conf = res.boxes.conf.numpy()
        ref_cls = res.boxes.cls.numpy().astype(int)
        keep = ref_conf >= spec.thresholds[ref_cls]
        ref_xyxy, ref_conf, ref_cls = ref_xyxy[keep], ref_conf[keep], ref_cls[keep]

        chw, info = preprocess(cv2.imread(str(path)), spec.input_size)
        mine = decode(detector.infer(model, chw[None])[0], spec, info)

        for c in range(spec.nc):
            r, m = ref_cls == c, mine.cls == c
            total += int(m.sum())
            unmatched = []
            if r.any() and m.any():
                ious = iou_matrix(ref_xyxy[r], mine.xyxy[m])
                used = set()
                for i in range(ious.shape[0]):
                    j = next((j for j in np.argsort(-ious[i]) if j not in used and ious[i, j] >= PAIR_IOU), None)
                    if j is None:
                        unmatched.append(("ultralytics", ref_conf[r][i]))
                    else:
                        used.add(j)
                unmatched += [("сервис", mine.conf[m][j]) for j in range(int(m.sum())) if j not in used]
            else:
                unmatched = [("ultralytics", s) for s in ref_conf[r]] + [("сервис", s) for s in mine.conf[m]]
            for side, conf in unmatched:
                if abs(float(conf) - float(spec.thresholds[c])) <= NEAR_THRESHOLD:
                    borderline += 1
                else:
                    hard.append(f"{path.name}: {spec.classes[c]} только у {side}, оценка {conf:.3f}")

    print(f"\n{model}: {len(images)} картинок, рамок {total}, расхождений у порога {borderline}, прочих {len(hard)}")
    assert total >= N_IMAGES, f"слишком мало рамок ({total}), тест ничего не проверяет"
    assert not hard, "рамки расходятся с ultralytics:\n" + "\n".join(hard)
    assert borderline <= MAX_BORDERLINE


def test_ppe_matches_ultralytics_on_css_test(real_specs):
    images = pick(train_dir().parent / "merged" / "ppe" / "images" / "test", "css_*")
    compare("ppe", images, real_specs)


def test_hazard_matches_ultralytics_on_test_split(real_specs):
    images = pick(train_dir().parent / "merged" / "hazard" / "images" / "test", "*.jpg")
    compare("hazard", images, real_specs)
