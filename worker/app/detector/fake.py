from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import yaml

from worker.app.model_spec import ModelSpec

ANCHORS = 8400


@dataclass(frozen=True)
# для тестов. рамки в долях кадра как в контрактах
class FakeBox:
    cls: str
    bbox: tuple[float, float, float, float]  # x y w h
    conf: float = 0.9


Step = Sequence[FakeBox]
ScenarioFn = Callable[[str, int, int], Step]


class FakeDetector:
    name = "fake"
    device = "fake"

    def __init__(self, specs: dict[str, ModelSpec], scenario: dict[str, Sequence[Step]] | ScenarioFn | None = None,
                 loop: bool = True):
        self.specs = specs
        self.warnings: list[str] = []
        self._calls: dict[str, int] = {}
        if callable(scenario):
            self._fn = scenario
        else:
            steps = scenario or {}

            def from_steps(model: str, call: int, _item: int) -> Step:
                seq = steps.get(model) or []
                if not seq:
                    return []
                if call >= len(seq) and not loop:
                    return []
                return seq[call % len(seq)]

            self._fn = from_steps

    @classmethod
    def from_yaml(cls, specs: dict[str, ModelSpec], path: Path) -> "FakeDetector":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        loop = bool(raw.pop("loop", True))
        scenario = {
            model: [[FakeBox(b["cls"], tuple(b["bbox"]), float(b.get("conf", 0.9))) for b in step] for step in steps]
            for model, steps in raw.items()
        }
        return cls(specs, scenario, loop=loop)

    def batch_limit(self, model: str) -> int | None:
        return None

    def infer(self, model: str, batch: np.ndarray) -> np.ndarray:
        spec = self.specs[model]
        call = self._calls.get(model, 0)
        self._calls[model] = call + 1
        size = spec.input_size
        out = np.zeros((len(batch), 4 + spec.nc, ANCHORS), dtype=np.float32)
        for item in range(len(batch)):
            for k, box in enumerate(self._fn(model, call, item)):
                x, y, w, h = box.bbox
                out[item, :4, k] = ((x + w / 2) * size, (y + h / 2) * size, w * size, h * size)
                out[item, 4 + spec.class_id(box.cls), k] = box.conf
        return out
