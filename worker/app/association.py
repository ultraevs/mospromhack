from __future__ import annotations

from typing import Sequence

import numpy as np

HEAD_TOP_FRACTION = 0.45  # каска/маска только в верхних 45% человека
MIN_INSIDE = 0.5  # хотя бы половина рамки сиз внутри человека


def inside_fraction(item: np.ndarray, person: np.ndarray) -> float:
    w = min(item[2], person[2]) - max(item[0], person[0])
    h = min(item[3], person[3]) - max(item[1], person[1])
    area = (item[2] - item[0]) * (item[3] - item[1])
    if w <= 0 or h <= 0 or area <= 0:
        return 0.0
    return float(w * h / area)


def assign_items(people: np.ndarray, items: np.ndarray, is_head: Sequence[bool]) -> list[int]:
    out = []
    for item, head in zip(items, is_head):
        best, best_frac = -1, 0.0
        cy = (item[1] + item[3]) / 2
        for p, person in enumerate(people):
            frac = inside_fraction(item, person)
            if frac < MIN_INSIDE or frac <= best_frac:
                continue
            if head and cy > person[1] + HEAD_TOP_FRACTION * (person[3] - person[1]):
                continue
            best, best_frac = p, frac
        out.append(best)
    return out


# ray casting
def point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def anchor(bbox_xywh: Sequence[float]) -> tuple[float, float]:
    x, y, w, h = bbox_xywh
    return x + w / 2, y + h


def iou_xywh(a: Sequence[float], b: Sequence[float]) -> float:
    iw = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    ih = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)
