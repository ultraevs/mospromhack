from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from worker.app.postprocess import iou_matrix


@dataclass
class _Track:
    track_id: int
    xyxy: np.ndarray
    velocity: np.ndarray  # px/сек
    last_t: float


# свой простой трекер по iou + предсказание сдвига. bytetrack из supervision терял id на быстро идущих
class IouTracker:
    def __init__(self, iou_threshold: float = 0.25, max_age_s: float = 1.5, velocity_smoothing: float = 0.5,
                 max_center_shift: float = 1.0):
        self.iou_threshold = iou_threshold
        self.max_center_shift = max_center_shift
        self.max_age_s = max_age_s
        self.smoothing = velocity_smoothing
        self._tracks: list[_Track] = []
        self._next_id = 1

    def update(self, xyxy: np.ndarray, t: float) -> np.ndarray:
        xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        self._tracks = [tr for tr in self._tracks if t - tr.last_t <= self.max_age_s]
        ids = np.full(len(xyxy), -1, dtype=np.int64)

        if self._tracks and len(xyxy):
            predicted = np.stack([self._predict(tr, t) for tr in self._tracks])
            ious = iou_matrix(predicted, xyxy)
            used_tracks, used_dets = set(), set()
            for flat in np.argsort(-ious, axis=None):
                i, j = divmod(int(flat), ious.shape[1])
                if ious[i, j] < self.iou_threshold:
                    break
                if i in used_tracks or j in used_dets:
                    continue
                used_tracks.add(i)
                used_dets.add(j)
                self._assign(self._tracks[i], xyxy[j], t)
                ids[j] = self._tracks[i].track_id

            pairs = []
            for i, tr in enumerate(self._tracks):
                if i in used_tracks:
                    continue
                pw, ph = predicted[i, 2] - predicted[i, 0], predicted[i, 3] - predicted[i, 1]
                pc = (predicted[i, :2] + predicted[i, 2:]) / 2
                for j in np.flatnonzero(ids < 0):
                    w, h = xyxy[j, 2] - xyxy[j, 0], xyxy[j, 3] - xyxy[j, 1]
                    ratio = (w * h) / max(float(pw * ph), 1e-6)
                    dist = float(np.linalg.norm((xyxy[j, :2] + xyxy[j, 2:]) / 2 - pc)) / max(float(min(pw, ph)), 1e-6)
                    # При падении рамка меняет форму: вертикальная рамка
                    # превращается в широкую. Сохраняем ID только при близком
                    # центре и явной смене ориентации; обычный fallback остаётся
                    # строгим, чтобы не склеивать разных людей.
                    old_w = self._tracks[i].xyxy[2] - self._tracks[i].xyxy[0]
                    old_h = self._tracks[i].xyxy[3] - self._tracks[i].xyxy[1]
                    posture_change = old_h > old_w * 1.5 and w > h * 1.1 and 0.2 <= ratio <= 4.0
                    size_compatible = 0.5 <= ratio <= 2.0 or posture_change
                    if dist <= self.max_center_shift and size_compatible:
                        pairs.append((dist, i, int(j)))
            for _, i, j in sorted(pairs):
                if i in used_tracks or j in used_dets:
                    continue
                used_tracks.add(i)
                used_dets.add(j)
                self._assign(self._tracks[i], xyxy[j], t)
                ids[j] = self._tracks[i].track_id

        for j in np.flatnonzero(ids < 0):
            ids[j] = self._next_id
            self._tracks.append(_Track(self._next_id, xyxy[j].copy(), np.zeros(2, np.float32), t))
            self._next_id += 1
        return ids

    @staticmethod
    def _predict(tr: _Track, t: float) -> np.ndarray:
        shift = tr.velocity * (t - tr.last_t)
        return tr.xyxy + np.array([shift[0], shift[1], shift[0], shift[1]], dtype=np.float32)

    def _assign(self, tr: _Track, box: np.ndarray, t: float) -> None:
        dt = t - tr.last_t
        if dt > 0:
            old_c = (tr.xyxy[:2] + tr.xyxy[2:]) / 2
            new_c = (box[:2] + box[2:]) / 2
            tr.velocity = self.smoothing * tr.velocity + (1 - self.smoothing) * (new_c - old_c) / dt
        tr.xyxy = box.copy()
        tr.last_t = t


# скорость техники: сдвиг центра за ~1 с в своих высотах рамки в секунду, со сглаживанием.
# id даёт тот же iou-трекер, отдельный экземпляр на камеру
class VehicleSpeeds:
    def __init__(self, span_s: float = 1.0, smoothing: float = 0.5):
        self.tracker = IouTracker(max_age_s=2.0)
        self.span_s = span_s
        self.smoothing = smoothing
        self._hist: dict[int, list[tuple[float, float, float]]] = {}  # id -> (t, cx, cy)
        self._speed: dict[int, float] = {}

    def update(self, xyxy: np.ndarray, t: float) -> tuple[np.ndarray, list[float]]:
        xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        ids = self.tracker.update(xyxy, t)
        speeds = []
        for box, tid in zip(xyxy, ids):
            tid = int(tid)
            cx, cy, h = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, max(float(box[3] - box[1]), 1.0)
            hist = self._hist.setdefault(tid, [])
            hist.append((t, float(cx), float(cy)))
            while len(hist) > 2 and hist[1][0] <= t - self.span_s:
                hist.pop(0)
            t0, x0, y0 = hist[0]
            if t - t0 > 0:
                raw = float(np.hypot(cx - x0, cy - y0)) / h / (t - t0)
                prev = self._speed.get(tid)
                self._speed[tid] = raw if prev is None else self.smoothing * prev + (1 - self.smoothing) * raw
            speeds.append(self._speed.get(tid, 0.0))
        alive = {int(i) for i in ids} | {tr.track_id for tr in self.tracker._tracks}
        self._hist = {k: v for k, v in self._hist.items() if k in alive}
        self._speed = {k: v for k, v in self._speed.items() if k in alive}
        return ids, speeds
