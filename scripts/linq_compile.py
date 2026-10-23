#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import onnx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.app.model_spec import load_model_spec  # noqa: E402
from worker.app.postprocess import preprocess  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def remove_initialized_inputs(model: onnx.ModelProto) -> None:
    inits = {i.name for i in model.graph.initializer}
    for inp in [i for i in model.graph.input if i.name in inits]:
        model.graph.input.remove(inp)


def auto_cut(model: onnx.ModelProto) -> list[str]:
    # режем на Div_1/Sub_1/Sigmoid как у yolo12m в SDK: на cpu остаются два Concat и умножение на страйды
    producer = {o: n for n in model.graph.node for o in n.output}
    out = model.graph.output[0].name
    final = producer[out]
    if final.op_type != "Concat" or len(final.input) != 2:
        raise SystemExit(f"auto-срез: {out} получается из {final.op_type}, ожидался Concat из двух входов")
    box, cls = (producer.get(i) for i in final.input)
    if cls is None or cls.op_type != "Sigmoid":
        raise SystemExit(f"auto-срез: второй вход финального Concat — {cls and cls.op_type}, ожидался Sigmoid")
    if box is None or box.op_type != "Mul":
        raise SystemExit(f"auto-срез: первый вход финального Concat — {box and box.op_type}, ожидался Mul на страйды")
    inner = next((producer[i] for i in box.input if i in producer and producer[i].op_type == "Concat"), None)
    if inner is None:
        raise SystemExit("auto-срез: перед Mul нет Concat(xy, wh)")
    return [*inner.input, cls.output[0]]


def tail_strides(model: onnx.ModelProto) -> np.ndarray:
    producer = {o: n for n in model.graph.node for o in n.output}
    inits = {i.name: i for i in model.graph.initializer}
    mul = producer[producer[model.graph.output[0].name].input[0]]
    for name in mul.input:
        if name in inits:
            return onnx.numpy_helper.to_array(inits[name]).astype(np.float32)
        node = producer.get(name)
        if node is not None and node.op_type == "Constant":
            return onnx.numpy_helper.to_array(node.attribute[0].t).astype(np.float32)
    raise SystemExit("auto-срез: не нашёл константу страйдов у Mul")


def drop_unused_initializers(model: onnx.ModelProto) -> None:
    # get_sub_model тащит все веса, хвост из трёх операций весил 45 МБ
    used = {i for n in model.graph.node for i in n.input}
    for init in [i for i in model.graph.initializer if i.name not in used]:
        model.graph.initializer.remove(init)


def describe(desc) -> str:
    return ", ".join(f"{k}={getattr(desc, k, None)}" for k in ("user_shape", "user_shape_padded", "scale_shape"))


def load_calibration(path: Path, n: int, size: int) -> np.ndarray:
    frames = np.load(path)
    if n:
        frames = frames[:: max(1, len(frames) // n)][:n]
    batch = np.empty((len(frames), 3, size, size), np.float32)
    for i, frame in enumerate(frames):
        preprocess(frame, size, out=batch[i])
    return batch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="ppe | hazard")
    ap.add_argument("--preset", default="o1", choices=["o1", "o3", "o5", "o5fgs"])
    ap.add_argument("--calib", type=Path, help="data/linq/calib_<model>.npy от linq_calib.py")
    ap.add_argument("--n-calib", type=int, default=64, help="сколько кадров калибровки брать; 0 — все")
    ap.add_argument("--samplewise", type=float, help="samplewise_percentile для calibrate_torch, например 99.9")
    ap.add_argument("--act-percentile", type=float, help="activation_percentile для calibrate_torch")
    ap.add_argument("--finetune", type=int, default=0, help="эпох fine_tune_torch; 0 — без дообучения порогов")
    ap.add_argument("--cut", default="auto", help="auto | none | имена тензоров через запятую")
    ap.add_argument("--stage", default="all", choices=["quant", "all"], help="quant — без компиляции")
    args = ap.parse_args()

    import tpu_framework
    from tpu_framework import (O1, O3, O5, TPU_H1M, Network, QuantizedModel, RegularModel, TpuProgram, compiler,
                               get_sub_model, preliminary_model_conditioning)

    # O5_FGS в доке есть, а в 2026.9.45 не экспортируется
    presets = {"o1": O1, "o3": O3, "o5": O5, "o5fgs": getattr(tpu_framework, "O5_FGS", None)}
    if presets[args.preset] is None:
        raise SystemExit(f"пресета {args.preset} нет в этой версии SDK")
    spec = load_model_spec(ROOT / "models" / args.model)
    out_dir = ROOT / "models" / args.model / "linq"
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    model = onnx.load(str(spec.onnx_path))
    remove_initialized_inputs(model)
    inputs = {i.name: [d.dim_value for d in i.type.tensor_type.shape.dim] for i in model.graph.input}
    if any(0 in s for s in inputs.values()):
        raise SystemExit(f"динамическая форма входа {inputs}: компилируем только статичный best.onnx")
    input_name = next(iter(inputs))
    final_output = model.graph.output[0].name

    cut = [] if args.cut == "none" else auto_cut(model) if args.cut == "auto" else args.cut.split(",")
    tail_path = strides_path = None
    if args.cut == "auto":
        strides_path = out_dir / "strides.npy"
        np.save(strides_path, tail_strides(model))
    if cut:
        head = get_sub_model(model=model, ordered_input_names=[input_name], ordered_output_names=cut)
        tail = get_sub_model(model=model, ordered_input_names=cut, ordered_output_names=[final_output])
        drop_unused_initializers(tail)
        tail_path = out_dir / "tail.onnx"
        onnx.save(tail, str(tail_path))
        model = head
        print(f"срез: на TPU до {cut}, хвост -> {tail_path.relative_to(ROOT)}")

    t0 = time.perf_counter()
    model = preliminary_model_conditioning(model)
    opt_path = out_dir / "head_opt.onnx"
    onnx.save(model, str(opt_path))
    timings["optimization_s"] = time.perf_counter() - t0
    print(f"1/3 оптимизация: {timings['optimization_s']:.1f} с")

    t0 = time.perf_counter()
    if args.calib:
        calib = load_calibration(args.calib, args.n_calib, spec.input_size)
    else:
        print("!!! калибровка на шуме — только для проверки, что граф компилируется")
        calib = np.random.rand(4, 3, spec.input_size, spec.input_size).astype(np.float32)
    regular = RegularModel(model=model)
    mapping = getattr(regular, "anchors_mapping", None) or {}
    calib_dict = {mapping.get(input_name, input_name): calib}
    kwargs = {}
    if args.samplewise is not None:
        kwargs["samplewise_percentile"] = args.samplewise
    if args.act_percentile is not None:
        kwargs["activation_percentile"] = args.act_percentile
    thresholds = regular.calibrate_torch(calibration_data=calib_dict, **kwargs)
    if args.finetune:
        thresholds = regular.fine_tune_torch(thresholds=thresholds, fine_tuning_data=calib_dict,
                                             fine_tuning_epochs=args.finetune)
    RegularModel.dump_thresholds(str(out_dir / "thresholds.json"), thresholds)
    quant = regular.quantize(thresholds)
    quant_name = f"quant_{args.model}.qm.onnx"
    quant.save(file_dir=str(out_dir), file_name=quant_name)
    timings["quantization_s"] = time.perf_counter() - t0
    print(f"2/3 квантование на {len(calib)} кадрах: {timings['quantization_s']:.1f} с")

    meta = {
        "model": args.model,
        "source_onnx": spec.onnx_path.name,
        "input": input_name,
        "tpu_outputs": cut or [final_output],
        "tail_onnx": tail_path.name if tail_path else None,
        "tail": "yolo_detect" if strides_path else ("onnx" if tail_path else None),
        "strides": strides_path.name if strides_path else None,
        "quantized": quant_name,
        "calibration": {
            "file": str(args.calib) if args.calib else "random",
            "frames": int(len(calib)),
            "samplewise_percentile": args.samplewise,
            "activation_percentile": args.act_percentile,
            "finetune_epochs": args.finetune,
        },
    }

    if args.stage == "all":
        t0 = time.perf_counter()
        network, _ = Network.from_quantized_model(QuantizedModel.load(str(out_dir / quant_name)))
        executable, descriptions = compiler.compile_(hardware_parameters=TPU_H1M, network=network,
                                                     parameters=presets[args.preset])
        program_name = f"{args.model}_b1_{args.preset}_h1m.tpu"
        TpuProgram.from_executable(executable, descriptions).to_file(str(out_dir / program_name))
        timings["compilation_s"] = time.perf_counter() - t0
        print(f"3/3 компиляция {args.preset}: {timings['compilation_s']:.1f} с -> {program_name}")
        print("тензоры программы:")
        for name, desc in descriptions.items():
            print(f"  {name}: {describe(desc)}")
        meta["program"] = program_name
        meta["preset"] = args.preset
    meta["timings"] = {k: round(v, 1) for k, v in timings.items()}
    meta_path = out_dir / ("meta.yaml" if args.stage == "all" else "meta.quant.yaml")
    meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(json.dumps(meta["timings"]))
    print(f"готово: {meta_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
