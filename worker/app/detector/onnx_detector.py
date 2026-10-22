from __future__ import annotations

import logging

import numpy as np
import onnxruntime as ort

from worker.app.model_spec import ModelSpec

log = logging.getLogger("worker.detector")

DYNAMIC_NAME = "best.dynamic.onnx"


class OnnxDetector:
    name = "onnx"

    def __init__(self, specs: dict[str, ModelSpec], allow_cuda: bool = True):
        self.warnings: list[str] = []
        self._sessions: dict[str, ort.InferenceSession] = {}
        self._dynamic: dict[str, bool] = {}

        providers = ["CPUExecutionProvider"]
        if allow_cuda:
            try:
                # без этого cuda из колес не находится и молча едет на cpu
                ort.preload_dlls()
            except Exception as e:  # noqa: BLE001
                log.warning("onnxruntime.preload_dlls() не сработал: %s", e)
            if "CUDAExecutionProvider" in ort.get_available_providers():
                providers.insert(0, "CUDAExecutionProvider")

        for name, spec in specs.items():
            dynamic = spec.onnx_path.with_name(DYNAMIC_NAME)
            path = dynamic if dynamic.exists() else spec.onnx_path
            if not path.exists():
                raise FileNotFoundError(f"нет {path}; запусти scripts/sync_models.ps1")
            session = ort.InferenceSession(str(path), providers=providers)
            self._sessions[name] = session
            self._dynamic[name] = not isinstance(session.get_inputs()[0].shape[0], int)
            log.info("модель %s: %s, провайдеры %s, batch %s", name, path.name, session.get_providers(),
                     "динамический" if self._dynamic[name] else "1")

        # смотрим что реально поднялось а не что просили
        active = {p for s in self._sessions.values() for p in s.get_providers()}
        self.device = "cuda" if all("CUDAExecutionProvider" in s.get_providers() for s in self._sessions.values()) else "cpu"
        if self.device == "cpu" and allow_cuda:
            msg = f"ИНФЕРЕНС ИДЁТ НА CPU: провайдер CUDA недоступен (активны {sorted(active)})"
            self.warnings.append(msg)
            log.error(msg)

    def batch_limit(self, model: str) -> int | None:
        return None if self._dynamic[model] else 1

    def infer(self, model: str, batch: np.ndarray) -> np.ndarray:
        session = self._sessions[model]
        input_name = session.get_inputs()[0].name
        batch = np.ascontiguousarray(batch, dtype=np.float32)
        if self._dynamic[model] or len(batch) == 1:
            return session.run(None, {input_name: batch})[0]
        return np.concatenate([session.run(None, {input_name: batch[i:i + 1]})[0] for i in range(len(batch))])
