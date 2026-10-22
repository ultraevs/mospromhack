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


def preprocess(img_bgr: np.ndarray, size: int) -> tuple[np.ndarray, LetterboxInfo]:
    boxed, info = letterbox(img_bgr, size)
    chw = np.ascontiguousarray(boxed[:, :, ::-1].transpose(2, 0, 1), dtype=np.float32) / 255.0
    return chw, info


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
