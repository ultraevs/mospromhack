from __future__ import annotations

import logging

import numpy as np
import onnxruntime as ort

from worker.app.model_spec import ModelSpec

log = logging.getLogger("worker.detector")

DYNAMIC_NAME = "best.dynamic.onnx"
# при смене размера батча cuda заново готовит свертки и все в 3 раза медленнее (19мс vs 7 на 3060). поэтому полные пачки в динамическую, хвост по одному
CHUNK = 4


class OnnxDetector:
    name = "onnx"

    def __init__(self, specs: dict[str, ModelSpec], allow_cuda: bool = True, prefer_dynamic: bool = True):
        self.warnings: list[str] = []
        self._single: dict[str, ort.InferenceSession] = {}
        self._chunked: dict[str, ort.InferenceSession] = {}

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
            if not spec.onnx_path.exists():
                raise FileNotFoundError(f"нет {spec.onnx_path}; запусти scripts/sync_models.ps1")
            self._single[name] = ort.InferenceSession(str(spec.onnx_path), providers=providers)
            dynamic = spec.onnx_path.with_name(DYNAMIC_NAME)
            if prefer_dynamic and dynamic.exists():
                self._chunked[name] = ort.InferenceSession(str(dynamic), providers=providers)
            log.info("модель %s: провайдеры %s, пачки по %s", name, self._single[name].get_providers(),
                     CHUNK if name in self._chunked else "1 (нет best.dynamic.onnx)")

        sessions = [*self._single.values(), *self._chunked.values()]
        # смотрим что реально поднялось а не что просили
        active = {p for s in sessions for p in s.get_providers()}
        self.device = "cuda" if all("CUDAExecutionProvider" in s.get_providers() for s in sessions) else "cpu"
        if self.device == "cpu" and allow_cuda:
            msg = f"ИНФЕРЕНС ИДЁТ НА CPU: провайдер CUDA недоступен (активны {sorted(active)})"
            self.warnings.append(msg)
            log.error(msg)

    def batch_limit(self, model: str) -> int | None:
        return CHUNK if model in self._chunked else 1

    def infer(self, model: str, batch: np.ndarray) -> np.ndarray:
        single = self._single[model]
        chunked = self._chunked.get(model)
        name = single.get_inputs()[0].name
        batch = np.ascontiguousarray(batch, dtype=np.float32)
        out, i = [], 0
        if chunked is not None:
            while len(batch) - i >= CHUNK:
                out.append(chunked.run(None, {name: batch[i:i + CHUNK]})[0])
                i += CHUNK
        for k in range(i, len(batch)):
            out.append(single.run(None, {name: batch[k:k + 1]})[0])
        return np.concatenate(out) if len(out) > 1 else out[0]
