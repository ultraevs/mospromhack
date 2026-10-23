from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from worker.app.model_spec import ModelSpec

PAD_VALUE = 114


@dataclass(frozen=True)
class LetterboxInfo:
    scale: float
    pad_x: int
    pad_y: int
    src_w: int
    src_h: int


@dataclass(frozen=True)
class Detections:
    xyxy: np.ndarray  # в пикселях исходного кадра
    conf: np.ndarray
    cls: np.ndarray

    def __len__(self) -> int:
        return len(self.conf)

    @staticmethod
    def empty() -> "Detections":
        return Detections(np.zeros((0, 4), np.float32), np.zeros(0, np.float32), np.zeros(0, np.int64))


def letterbox(img: np.ndarray, size: int) -> tuple[np.ndarray, LetterboxInfo]:
    h, w = img.shape[:2]
    scale = min(size / h, size / w)
    new_w, new_h = round(w * scale), round(h * scale)
    if (new_w, new_h) != (w, h):
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    # округляем как ultralytics иначе рамки съезжают на 1px
    pad_x = round((size - new_w) / 2 - 0.1)
    pad_y = round((size - new_h) / 2 - 0.1)
    out = np.full((size, size, 3), PAD_VALUE, dtype=np.uint8)
    out[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = img
    return out, LetterboxInfo(scale, pad_x, pad_y, w, h)


_255 = np.float32(255)


def preprocess(img_bgr: np.ndarray, size: int, out: np.ndarray | None = None) -> tuple[np.ndarray, LetterboxInfo]:
    boxed, info = letterbox(img_bgr, size)
    if out is None:
        out = np.empty((3, size, size), dtype=np.float32)
    for c in range(3):
        np.divide(boxed[:, :, 2 - c], _255, out=out[c], casting="unsafe")
    return out, info


def to_source(xyxy: np.ndarray, info: LetterboxInfo) -> np.ndarray:
    out = xyxy.astype(np.float32, copy=True)
    out[:, [0, 2]] = (out[:, [0, 2]] - info.pad_x) / info.scale
    out[:, [1, 3]] = (out[:, [1, 3]] - info.pad_y) / info.scale
    out[:, [0, 2]] = out[:, [0, 2]].clip(0, info.src_w)
    out[:, [1, 3]] = out[:, [1, 3]].clip(0, info.src_h)
    return out


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(rb - lt, 0, None).prod(axis=2)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)


def nms(xyxy: np.ndarray, scores: np.ndarray, iou_thr: float) -> np.ndarray:
    order = np.argsort(-scores, kind="stable")
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        ious = iou_matrix(xyxy[i:i + 1], xyxy[rest])[0]
        order = rest[ious <= iou_thr]
    return np.array(keep, dtype=np.int64)


def nms_per_class(xyxy: np.ndarray, scores: np.ndarray, cls: np.ndarray, iou_thr: float) -> np.ndarray:
    keep = [np.flatnonzero(cls == c)[nms(xyxy[cls == c], scores[cls == c], iou_thr)] for c in np.unique(cls)]
    if not keep:
        return np.zeros(0, dtype=np.int64)
    keep = np.concatenate(keep)
    return keep[np.argsort(-scores[keep], kind="stable")]


# выход yolov8: [4+nc, 8400], cx cy w h + скоры классов
def decode(output: np.ndarray, spec: ModelSpec, info: LetterboxInfo, max_det: int = 300) -> Detections:
    if output.shape[0] != 4 + spec.nc:
        raise ValueError(f"выход модели {spec.name}: {output.shape[0]} строк, ожидалось {4 + spec.nc}")
    scores = output[4:]
    cls = scores.argmax(axis=0)
    conf = scores[cls, np.arange(scores.shape[1])]
    mask = conf >= spec.thresholds[cls]
    if not mask.any():
        return Detections.empty()

    cx, cy, w, h = output[:4, mask]
    xyxy = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    conf, cls = conf[mask], cls[mask]
    keep = nms_per_class(xyxy, conf, cls, spec.nms_iou)[:max_det]
    return Detections(to_source(xyxy[keep], info), conf[keep].astype(np.float32), cls[keep].astype(np.int64))


COCO_TO_PPE = {"person": "person", "car": "vehicle", "truck": "vehicle", "bus": "vehicle", "motorcycle": "vehicle"}


# люди и техника из coco вместо своих, сизы оставляем от ppe
def merge_people(ppe: Detections, ppe_spec: ModelSpec, people: Detections, people_spec: ModelSpec) -> Detections:
    replaced = {ppe_spec.class_id(c) for c in set(COCO_TO_PPE.values()) if c in ppe_spec.classes}
    keep = ~np.isin(ppe.cls, list(replaced))
    mapped = [ppe_spec.class_id(COCO_TO_PPE[people_spec.classes[c]]) if people_spec.classes[c] in COCO_TO_PPE else -1
              for c in people.cls]
    sel = np.array([m >= 0 for m in mapped], dtype=bool)
    return Detections(
        np.concatenate([ppe.xyxy[keep], people.xyxy[sel]]).astype(np.float32),
        np.concatenate([ppe.conf[keep], people.conf[sel]]).astype(np.float32),
        np.concatenate([ppe.cls[keep], np.array(mapped, dtype=np.int64)[sel]]).astype(np.int64),
    )


# сигнальный цвет на торсе = жилет. модель на дальних камерах и со спины жилеты теряет
HIVIS_HSV = (((4, 140, 140), (22, 255, 255)), ((25, 110, 140), (45, 255, 255)))  # оранжевый, салатовый
HIVIS_MIN_FRACTION = 0.12


def hivis_vests(image: np.ndarray, dets: Detections, spec: ModelSpec) -> Detections:
    if "vest" not in spec.classes or "person" not in spec.classes:
        return dets
    person, vest = spec.class_id("person"), spec.class_id("vest")
    boxes, confs = [], []
    ih, iw = image.shape[:2]
    for (x1, y1, x2, y2), c in zip(dets.xyxy, dets.cls):
        if c != person:
            continue
        bh = y2 - y1
        tx1, tx2 = int(max(0, x1)), int(min(iw, x2))
        ty1, ty2 = int(max(0, y1 + 0.2 * bh)), int(min(ih, y1 + 0.6 * bh))
        if tx2 - tx1 < 4 or ty2 - ty1 < 4:
            continue
        hsv = cv2.cvtColor(image[ty1:ty2, tx1:tx2], cv2.COLOR_BGR2HSV)
        mask = sum(cv2.inRange(hsv, np.array(lo), np.array(hi)) > 0 for lo, hi in HIVIS_HSV)
        frac = float(np.count_nonzero(mask)) / mask.size
        if frac >= HIVIS_MIN_FRACTION:
            boxes.append([tx1, ty1, tx2, ty2])
            confs.append(min(0.9, 0.4 + frac))
    if not boxes:
        return dets
    return Detections(np.concatenate([dets.xyxy, np.array(boxes, np.float32)]).astype(np.float32),
                      np.concatenate([dets.conf, np.array(confs, np.float32)]).astype(np.float32),
                      np.concatenate([dets.cls, np.full(len(boxes), vest, np.int64)]).astype(np.int64))
