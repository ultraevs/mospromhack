from __future__ import annotations

import logging

import numpy as np
import onnxruntime as ort

from worker.app.model_spec import ModelSpec

log = logging.getLogger("worker.detector")

DYNAMIC_NAME = "best.dynamic.onnx"
FP16_NAME = "best.fp16.onnx"
# при смене размера батча cuda заново готовит свертки и все в 3 раза медленнее (19мс vs 7 на 3060). поэтому полные пачки в динамическую, хвост по одному
CHUNKS = (8, 4)


class OnnxDetector:
    name = "onnx"

    def __init__(self, specs: dict[str, ModelSpec], allow_cuda: bool = True, prefer_dynamic: bool = True,
                 precision: str = "fp32"):
        self.warnings: list[str] = []
        self.precision: dict[str, str] = {}
        self._single: dict[str, ort.InferenceSession] = {}
        self._chunked: dict[str, dict[int, ort.InferenceSession]] = {}

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
            half = spec.onnx_path.with_name(FP16_NAME)
            self.precision[name] = "fp32"
            if prefer_dynamic and precision in ("fp16", "auto") and "CUDAExecutionProvider" in providers:
                if half.exists():
                    dynamic, self.precision[name] = half, "fp16"
                elif precision == "fp16":
                    msg = f"модель {name}: нет {FP16_NAME}, работает float32; создай файл: python scripts/make_fp16.py"
                    self.warnings.append(msg)
                    log.warning(msg)
            if prefer_dynamic and dynamic.exists():
                self._chunked[name] = {n: ort.InferenceSession(str(dynamic), providers=providers) for n in CHUNKS}
            log.info("модель %s: провайдеры %s, пачки по %s, точность %s", name, self._single[name].get_providers(),
                     "/".join(map(str, CHUNKS)) if name in self._chunked else "1 (нет best.dynamic.onnx)",
                     self.precision[name])

        sessions = [*self._single.values(), *(s for by_size in self._chunked.values() for s in by_size.values())]
        # смотрим что реально поднялось а не что просили
        active = {p for s in sessions for p in s.get_providers()}
        self.device = "cuda" if all("CUDAExecutionProvider" in s.get_providers() for s in sessions) else "cpu"
        if self.device == "cpu" and allow_cuda:
            msg = f"ИНФЕРЕНС ИДЁТ НА CPU: провайдер CUDA недоступен (активны {sorted(active)})"
            self.warnings.append(msg)
            log.error(msg)

    def batch_limit(self, model: str) -> int | None:
        return CHUNKS[0] if model in self._chunked else 1

    def infer(self, model: str, batch: np.ndarray) -> np.ndarray:
        single = self._single[model]
        name = single.get_inputs()[0].name
        batch = np.ascontiguousarray(batch, dtype=np.float32)
        out, i = [], 0
        for size, session in self._chunked.get(model, {}).items():
            while len(batch) - i >= size:
                out.append(session.run(None, {name: batch[i:i + size]})[0])
                i += size
        for k in range(i, len(batch)):
            out.append(single.run(None, {name: batch[k:k + 1]})[0])
        return np.concatenate(out) if len(out) > 1 else out[0]
