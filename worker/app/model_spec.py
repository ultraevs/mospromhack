from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml


@dataclass(frozen=True)
class ModelSpec:
    name: str
    classes: tuple[str, ...]
    thresholds: np.ndarray  # по классам, порядок как в classes
    nms_iou: float
    input_size: int
    onnx_path: Path
    keypoints: int = 0  # у позы 17, выход 4 + nc + 17*3

    @property
    def nc(self) -> int:
        return len(self.classes)

    def class_id(self, name: str) -> int:
        try:
            return self.classes.index(name)
        except ValueError:
            raise KeyError(f"в модели {self.name} нет класса {name!r}; есть {list(self.classes)}") from None

    def threshold(self, name: str) -> float:
        return float(self.thresholds[self.class_id(name)])


# model.yaml генерит evaluate.py из обучения
def load_model_spec(model_dir: Path) -> ModelSpec:
    model_dir = Path(model_dir)
    path = model_dir / "model.yaml"
    if not path.exists():
        raise FileNotFoundError(f"нет {path}; запусти scripts/sync_models.ps1")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    classes = tuple(raw["classes"])
    missing = [c for c in classes if c not in raw["thresholds"]]
    if missing:
        raise ValueError(f"{path}: нет порогов для классов {missing}")
    shape = raw["input"]["shape"]
    if shape[2] != shape[3]:
        raise ValueError(f"{path}: ожидается квадратный вход, а в файле {shape}")
    kpt = int(raw.get("keypoints", 0))
    expected_out = [1, 4 + len(classes) + kpt * 3, raw["output"]["shape"][2]]
    if list(raw["output"]["shape"]) != expected_out:
        raise ValueError(f"{path}: выход {raw['output']['shape']} не согласуется с числом классов {len(classes)}")

    return ModelSpec(
        name=raw["model"],
        classes=classes,
        thresholds=np.array([raw["thresholds"][c] for c in classes], dtype=np.float32),
        nms_iou=float(raw["nms_iou"]),
        input_size=int(shape[2]),
        onnx_path=model_dir / "best.onnx",
        keypoints=kpt,
    )


def load_all_specs(models_dir: Path) -> dict[str, ModelSpec]:
    specs = {}
    for sub in sorted(Path(models_dir).iterdir()) if Path(models_dir).exists() else []:
        if (sub / "model.yaml").exists():
            spec = load_model_spec(sub)
            if not spec.keypoints:  # поза идёт мимо детекторов, см. load_pose_spec
                specs[spec.name] = spec
    return specs


def load_pose_spec(models_dir: Path) -> ModelSpec | None:
    path = Path(models_dir) / "pose"
    if not (path / "model.yaml").exists() or not (path / "best.onnx").exists():
        return None
    spec = load_model_spec(path)
    return spec if spec.keypoints else None
