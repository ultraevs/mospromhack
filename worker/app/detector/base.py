from __future__ import annotations

from typing import Protocol

import numpy as np


class Detector(Protocol):
    name: str
    device: str

    # [N,3,640,640] rgb 0..1 -> [N, 4+nc, 8400]
    def infer(self, model: str, batch: np.ndarray) -> np.ndarray:
        ...
