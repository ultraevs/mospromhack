from __future__ import annotations

import numpy as np

from worker.app.model_spec import ModelSpec


class LinqDetector:
    name = "linq"
    device = "linq"

    def __init__(self, specs: dict[str, ModelSpec]):
        self.specs = specs
        self.warnings: list[str] = []

    def batch_limit(self, model: str) -> int | None:
        return 1

    # TODO: доступ к железу так и не дали, см docs/LINQ.md
    def infer(self, model: str, batch: np.ndarray) -> np.ndarray:
        raise NotImplementedError(
            "LinqDetector не реализован: нет доступа к ускорителю и его SDK. См. docs/LINQ.md; "
            "пока используй worker.detector: onnx в config.yaml."
        )
