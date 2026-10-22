from __future__ import annotations

import numpy as np

from worker.app.postprocess import Detections, nms_per_class

MIN_SIDE = 8


# кропы людей в исходном разрешении - сигарету на общем кадре не видно
def crop_box(xyxy, top_fraction: float, margin: float, frame_w: int, frame_h: int) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    w, h = x2 - x1, (y2 - y1) * top_fraction
    cx1, cy1 = max(0, int(x1 - w * margin)), max(0, int(y1 - h * margin))
    cx2, cy2 = min(frame_w, int(x2 + w * margin)), min(frame_h, int(y1 + h + h * margin))
    if cx2 - cx1 < MIN_SIDE or cy2 - cy1 < MIN_SIDE:
        return None
    return cx1, cy1, cx2, cy2


def shift(dets: Detections, dx: float, dy: float) -> Detections:
    if not len(dets):
        return dets
    return Detections(dets.xyxy + np.array([dx, dy, dx, dy], np.float32), dets.conf, dets.cls)


def only_classes(dets: Detections, class_ids: set[int]) -> Detections:
    keep = np.isin(dets.cls, list(class_ids))
    return Detections(dets.xyxy[keep], dets.conf[keep], dets.cls[keep])


def merge(a: Detections, b: Detections, iou: float) -> Detections:
    if not len(b):
        return a
    xyxy = np.concatenate([a.xyxy, b.xyxy])
    conf = np.concatenate([a.conf, b.conf])
    cls = np.concatenate([a.cls, b.cls])
    keep = nms_per_class(xyxy, conf, cls, iou)
    return Detections(xyxy[keep], conf[keep], cls[keep])
