from __future__ import annotations

import logging
import sys
import time

import numpy as np

from shared.config import get_settings
from worker.app.detector.onnx_detector import OnnxDetector
from worker.app.model_spec import load_all_specs


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    specs = load_all_specs(get_settings().models_dir)
    if not specs:
        print("нет моделей в models/; запусти scripts/sync_models.ps1")
        return 1
    detector = OnnxDetector(specs)
    for name, spec in specs.items():
        batch = np.random.default_rng(0).random((1, 3, spec.input_size, spec.input_size), dtype=np.float32)
        detector.infer(name, batch)  # прогрев
        t = time.perf_counter()
        for _ in range(20):
            out = detector.infer(name, batch)
        ms = (time.perf_counter() - t) / 20 * 1000
        print(f"{name}: выход {out.shape}, {ms:.1f} мс на кадр")
    print(f"устройство: {detector.device}")
    return 0 if detector.device == "cuda" else 1


if __name__ == "__main__":
    sys.exit(main())
