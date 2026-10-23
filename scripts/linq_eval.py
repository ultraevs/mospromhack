#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_map import evaluate  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _Input:
    def __init__(self, name: str):
        self.name = name


class FloatSession:

    def __init__(self, path: Path):
        self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.name = self.session.get_inputs()[0].name

    def get_inputs(self) -> list[_Input]:
        return [_Input(self.name)]

    def run(self, _outputs, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        batch = next(iter(feed.values()))
        return [np.concatenate([self.session.run(None, {self.name: x[None]})[0] for x in batch])]


class QuantSession:

    def __init__(self, linq_dir: Path, meta: dict):
        import torch
        from dnn_quant.api import QuantizedModel, TpuDtype

        self.torch, self.dtype = torch, TpuDtype
        self.quant = QuantizedModel.load(str(linq_dir / meta["quantized"]))
        graph = self.quant.as_torch_graph(batch_size=1, cache_word_length=128)
        self.gm = graph.gm.eval()
        self.anchors = graph.get_outputs_anchors() | graph.get_inputs_anchors()
        self.order_in = list(graph._input_layer_names)
        self.input = meta["input"]
        self.outputs = meta["tpu_outputs"]
        self.tail = (ort.InferenceSession(str(linq_dir / meta["tail_onnx"]), providers=["CPUExecutionProvider"])
                     if meta.get("tail_onnx") else None)

    def get_inputs(self) -> list[_Input]:
        return [_Input(self.input)]

    def head(self, x: np.ndarray) -> dict[str, np.ndarray]:
        data = self.quant.rescale({self.anchors[self.input]: x[None]}, self.dtype.Input)
        with self.torch.no_grad():
            out = self.gm(*(self.torch.as_tensor(data[n]) for n in self.order_in))
        if not isinstance(out, (tuple, list)):
            out = (out,)
        raw = {self.anchors.get(n, n): o.detach().cpu().numpy() for n, o in zip(self.outputs, out)}
        scaled = self.quant.rescale(raw, self.dtype.Output)
        return {n: scaled[self.anchors.get(n, n)] for n in self.outputs}

    def run(self, _outputs, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        batch = next(iter(feed.values()))
        result = []
        for x in batch:
            parts = self.head(x)
            result.append(self.tail.run(None, parts)[0] if self.tail else parts[self.outputs[0]])
        return [np.concatenate(result)]


def save_reference(spec, linq_dir: Path, meta: dict, images: list[Path]) -> None:
    import cv2

    from worker.app.postprocess import letterbox, preprocess

    frames = np.stack([letterbox(cv2.imread(str(p)), spec.input_size)[0] for p in images])
    batch = np.stack([preprocess(f, spec.input_size)[0] for f in frames])
    floats = FloatSession(spec.onnx_path).run(None, {"images": batch})[0]
    quant = QuantSession(linq_dir, meta)
    parts = [quant.head(x) for x in batch]
    out = ROOT / "data" / "linq" / f"ref_{spec.name}.npz"
    np.savez_compressed(out, frames=frames, float32=floats, int8=quant.run(None, {"images": batch})[0],
                        **{f"head:{n}": np.concatenate([p[n] for p in parts]) for n in meta["tpu_outputs"]})
    print(f"{out}: {len(frames)} кадров, float32 {floats.shape}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--merged", required=True, type=Path, help="папка merged с images/val и labels/val")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--meta", default="meta.yaml", help="meta.yaml или meta.quant.yaml в models/<model>/linq")
    ap.add_argument("--ref", type=int, default=0,
                    help="вместо mAP сохранить N кадров val с выходами float и int8 в data/linq/ref_<model>.npz "
                         "(эталон для сверки с TPU на стенде, см. linq_bench.py)")
    args = ap.parse_args()

    spec = load_model_spec(ROOT / "models" / args.model)
    linq_dir = ROOT / "models" / args.model / "linq"
    meta = yaml.safe_load((linq_dir / args.meta).read_text(encoding="utf-8"))
    images = sorted((args.merged / args.model / "images" / "val").iterdir())
    if args.limit:
        images = images[:: max(1, len(images) // args.limit)][:args.limit]
    labels = args.merged / args.model / "labels" / "val"

    if args.ref:
        save_reference(spec, linq_dir, meta, images[:: max(1, len(images) // args.ref)][:args.ref])
        return

    results = {
        "float32": evaluate(FloatSession(spec.onnx_path), spec, images, labels),
        "linq int8": evaluate(QuantSession(linq_dir, meta), spec, images, labels),
    }
    print(f"\n{args.model}: val, {len(images)} картинок, калибровка {meta['calibration']}")
    print("| класс | AP50 float32 | AP50 linq int8 | разница |")
    print("|---|---|---|---|")
    base, quant = results["float32"][0], results["linq int8"][0]
    for cls in base:
        print(f"| {cls} | {base[cls]:.4f} | {quant[cls]:.4f} | {quant[cls] - base[cls]:+.4f} |")
    mb, mq = float(np.mean(list(base.values()))), float(np.mean(list(quant.values())))
    print(f"| **mAP50** | **{mb:.4f}** | **{mq:.4f}** | {mq - mb:+.4f} |")
    print(f"условие «mAP50 падает не больше чем на 0,03»: {'выполнено' if mb - mq <= 0.03 else 'НЕ выполнено'}")


if __name__ == "__main__":
    main()
