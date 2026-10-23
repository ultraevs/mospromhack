from __future__ import annotations

from pathlib import Path

from worker.app.detector.base import Detector
from worker.app.model_spec import ModelSpec

__all__ = ["Detector", "create_detector"]


def create_detector(kind: str, specs: dict[str, ModelSpec], fake_scenario: str | Path | None = None,
                    precision: str = "fp32", linq: dict | None = None) -> Detector:
    # импорты внутри чтобы fake работал без onnxruntime
    if kind == "onnx":
        from worker.app.detector.onnx_detector import OnnxDetector
        return OnnxDetector(specs, precision=precision)
    if kind == "fake":
        from worker.app.detector.fake import FakeDetector
        return FakeDetector.from_yaml(specs, Path(fake_scenario)) if fake_scenario else FakeDetector(specs)
    if kind == "linq":
        from worker.app.detector.linq import LinqDetector
        return LinqDetector(specs, **(linq or {}))
    raise ValueError(f"неизвестный детектор {kind!r}; допустимо: onnx, linq, fake")
