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
                    if dist <= self.max_center_shift and 0.5 <= ratio <= 2.0:
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
