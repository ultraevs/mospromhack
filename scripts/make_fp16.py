#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, numpy_helper
from onnxconverter_common import float16

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_settings  # noqa: E402

SOURCE, TARGET = "best.dynamic.onnx", "best.fp16.onnx"


def main() -> None:
    models = get_settings().models_dir
    for name in sys.argv[1:] or ["ppe", "hazard"]:
        src = models / name / SOURCE
        if not src.exists():
            raise SystemExit(f"нет {src}; сначала python scripts/export_dynamic.py")
        blocked = [op for op in float16.DEFAULT_OP_BLOCK_LIST if op != "Resize"]
        model = float16.convert_float_to_float16(onnx.load(str(src)), keep_io_types=True, op_block_list=blocked)
        service_inputs = {i for n in model.graph.node if n.op_type == "Resize" for i in n.input[1:3] if i}
        for node in model.graph.node:
            if node.op_type == "Constant" and node.output[0] in service_inputs:
                for attr in node.attribute:
                    if attr.name == "value" and attr.t.data_type == TensorProto.FLOAT16:
                        attr.t.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(attr.t).astype(np.float32), attr.t.name))
        for init in model.graph.initializer:
            if init.name in service_inputs and init.data_type == TensorProto.FLOAT16:
                init.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(init).astype(np.float32), init.name))
        original = {n.name for n in onnx.load(str(src)).graph.node}
        for node in model.graph.node:
            if node.op_type == "Cast" and node.name in original:
                for attr in node.attribute:
                    if attr.name == "to" and attr.i == TensorProto.FLOAT:
                        attr.i = TensorProto.FLOAT16
        onnx.checker.check_model(model)
        dst = models / name / TARGET
        onnx.save(model, str(dst))
        print(f"{name}: {src.stat().st_size / 1e6:.1f} МБ -> {dst.name} {dst.stat().st_size / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()
