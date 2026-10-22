#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_settings  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402


def main() -> int:
    from ultralytics import YOLO

    settings = get_settings()
    runs = Path(settings.train_runs_dir)
    specs = load_all_specs(settings.models_dir)
    if not specs:
        print("нет моделей в models/; запусти scripts/sync_models.ps1")
        return 1
    for name, spec in specs.items():
        weights = runs / name / "weights" / "best.pt"
        if not weights.exists():
            print(f"нет {weights}; проверь TRAIN_RUNS_DIR в .env")
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "best.pt"
            shutil.copyfile(weights, local)
            exported = YOLO(str(local)).export(format="onnx", opset=12, dynamic=True, simplify=True,
                                               imgsz=spec.input_size, device="cpu", verbose=False)
            target = spec.onnx_path.with_name("best.dynamic.onnx")
            shutil.move(exported, target)
        print(f"{name}: {target} ({target.stat().st_size / 1e6:.1f} МБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
