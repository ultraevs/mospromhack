from __future__ import annotations

# packing/unpacking для raw-режима на numpy: на стенде нет pytpu, а sdk-шный tpu_io только под py3.12.
# в облако уходит готовый int8 (1.6 МБ вместо 4.9 МБ float32), назад int8/fp16 вместо float32.
# раскладка взята из metadata.json программы, сверено с tpu_io.pack/unpack (tests/test_linq_raw.py)
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class RawTensor:
    name: str
    size: int
    user_shape: tuple[int, ...]
    user_order: tuple[str, ...]
    tpu_shape: tuple[int, ...]
    tpu_order: tuple[str, ...]
    tpu_dtype: np.dtype
    scales: np.ndarray  # уже в форме, которая бродкастится на user_shape
    zero_points: np.ndarray
    padding: tuple[tuple[int, int], ...]

    @property
    def padded_shape(self) -> tuple[int, ...]:
        return tuple(n + abs(a) + abs(b) for n, (a, b) in zip(self.user_shape, self.padding))

    def _user_to_tpu(self):
        # канальная ось user 'C' в tpu разбита на (C группы, B блок)
        c = self.user_order.index("C")
        cg = self.tpu_shape[self.tpu_order.index("C")]
        b = self.tpu_shape[self.tpu_order.index("B")]
        shape = list(self.padded_shape)
        split = shape[:c] + [cg, b] + shape[c + 1:]
        names = list(self.user_order[:c]) + ["C", "B"] + list(self.user_order[c + 1:])
        return split, names


def _scale_like(values, scale_shape, ndim) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32).reshape(scale_shape)
    return arr.reshape((1,) * (ndim - arr.ndim) + arr.shape)


def load_raw_meta(program: Path) -> tuple[dict, dict[str, RawTensor], dict[str, RawTensor]]:
    with zipfile.ZipFile(program) as z:
        meta = json.loads(z.read("metadata.json"))

    def parse(section) -> dict[str, RawTensor]:
        out = {}
        for group in meta[section].values():
            for name, t in group.items():
                nd = len(t["user_shape"])
                out[name] = RawTensor(
                    name=name, size=t["size"],
                    user_shape=tuple(t["user_shape"]), user_order=tuple(t["user_order"]),
                    tpu_shape=tuple(t["tpu_shape"]), tpu_order=tuple(t["tpu_order"]),
                    tpu_dtype=np.dtype(t["tpu_dtype"]),
                    scales=_scale_like(t["scales"], t["scale_shape"], nd),
                    zero_points=_scale_like(t["zero_points"], t["scale_shape"], nd),
                    padding=tuple(tuple(p) for p in t["padding"]),
                )
        return out

    return meta["hardware_parameters"], parse("inputs"), parse("outputs")


def _quant(x: np.ndarray, t: RawTensor) -> np.ndarray:
    if t.tpu_dtype == np.int8:
        # в float64, иначе половинки (94.5) округляются не туда, куда у sdk
        return np.clip(np.rint(x.astype(np.float64) * t.scales + t.zero_points), -128, 127).astype(np.int8)
    return (x * t.scales + t.zero_points).astype(t.tpu_dtype)


def pack(x: np.ndarray, t: RawTensor, dwl: int) -> np.ndarray:
    q = _quant(np.asarray(x, dtype=np.float32), t)
    q = np.pad(q, [(abs(a), abs(b)) for a, b in t.padding])
    split, names = t._user_to_tpu()
    q = q.reshape(split).transpose([names.index(n) for n in t.tpu_order])
    raw = np.ascontiguousarray(q).view(np.uint8).reshape(-1)
    if raw.size < t.size:
        raw = np.pad(raw, (0, t.size - raw.size))
    return raw.reshape(-1, dwl)


class ImagePacker:
    """bgr uint8 после letterbox -> raw вход без float: таблица на 256 значений, одна выборка.
    на стенде 1.5 ядра, float-препроцесс + pack там стоят ~1 с на кадр"""

    def __init__(self, t: RawTensor, dwl: int):
        self.t, self.dwl = t, dwl
        n, c, h, w = t.user_shape
        # быстрый путь только под раскладку images: NCHW -> (1, H, W, 1, B), каналы добиты нулями
        self.fast = (t.user_order == ("N", "C", "H", "W") and t.tpu_order == ("C", "H", "W", "N", "B")
                     and n == 1 and c == 3 and t.tpu_shape == (1, h, w, 1, t.tpu_shape[-1]) and t.tpu_shape[-1] >= 3
                     and t.scales.size == 1 and t.zero_points.size == 1 and h * w * t.tpu_shape[-1] == t.size)
        # ровно как preprocess (p / 255 во float32) и потом _quant
        x = (np.arange(256, dtype=np.float32) / np.float32(255)).reshape(1, 1, 1, 256)
        self.lut = _quant(x, t).reshape(256) if self.fast else None
        # одинаковая подпись = одинаковые байты на выходе, кэш упаковки делится между программами
        self.signature = (dwl, t.tpu_shape, t.tpu_order, t.user_order, t.scales.tobytes(), t.zero_points.tobytes())
        if self.fast:
            # 4 канала: r, g, b и нулевой паддинг; cv2.LUT по rgba в разы быстрее numpy-выборки
            b = t.tpu_shape[-1]
            lut = np.zeros((1, 256, 4), np.int8)
            lut[0, :, :3] = self.lut[:, None]
            self.lut4 = lut.view(np.uint8) if b == 4 else None

    def __call__(self, boxed_bgr: np.ndarray) -> np.ndarray:
        if not self.fast:
            x = boxed_bgr[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / np.float32(255)
            return pack(x, self.t, self.dwl)
        h, w = boxed_bgr.shape[:2]
        if self.lut4 is not None:
            import cv2
            return cv2.LUT(cv2.cvtColor(boxed_bgr, cv2.COLOR_BGR2RGBA), self.lut4).reshape(-1, self.dwl)
        out = np.zeros((h, w, self.t.tpu_shape[-1]), dtype=np.int8)
        out[:, :, :3] = self.lut[boxed_bgr[:, :, ::-1]]
        return out.view(np.uint8).reshape(-1, self.dwl)


def unpack(raw: np.ndarray, t: RawTensor) -> np.ndarray:
    split, names = t._user_to_tpu()
    tpu_shape = [split[names.index(n)] for n in t.tpu_order]
    n = int(np.prod(tpu_shape)) * t.tpu_dtype.itemsize
    q = np.frombuffer(np.ascontiguousarray(raw).reshape(-1)[:n].tobytes(), dtype=t.tpu_dtype).reshape(tpu_shape)
    q = q.transpose([t.tpu_order.index(n) for n in names]).reshape(t.padded_shape)
    q = q[tuple(slice(abs(a), abs(a) + s) for s, (a, _) in zip(t.user_shape, t.padding))]
    # на выходе scale множитель, на входе тоже множитель (images: x*127)
    return (q.astype(np.float32) - t.zero_points) * t.scales
