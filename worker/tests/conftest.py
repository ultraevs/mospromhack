from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared.config import get_settings
from worker.app.model_spec import ModelSpec, load_all_specs, load_model_spec


def write_model_yaml(folder: Path, name: str, thresholds: dict[str, float], nms_iou: float = 0.7) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    classes = list(thresholds)
    (folder / "model.yaml").write_text(yaml.safe_dump({
        "model": name,
        "weights": {"onnx": "weights/best.onnx", "pt": "weights/best.pt"},
        "input": {"name": "images", "shape": [1, 3, 640, 640], "layout": "NCHW", "color": "RGB",
                  "scale": "0..1", "letterbox": True},
        "output": {"name": "output0", "shape": [1, 4 + len(classes), 8400]},
        "nms_iou": nms_iou,
        "classes": classes,
        "thresholds": thresholds,
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return folder


@pytest.fixture
# выдуманные классы чтобы тесты не зависели от реальных моделей
def toy_spec(tmp_path) -> ModelSpec:
    return load_model_spec(write_model_yaml(tmp_path / "toy", "toy", {"alpha": 0.5, "beta": 0.2, "gamma": 0.8}))


@pytest.fixture(scope="session")
def real_specs() -> dict[str, ModelSpec]:
    specs = load_all_specs(get_settings().models_dir)
    if not {"ppe", "hazard"} <= set(specs) or not all(s.onnx_path.exists() for s in specs.values()):
        pytest.skip("нет моделей в models/; запусти scripts/sync_models.ps1")
    return specs
