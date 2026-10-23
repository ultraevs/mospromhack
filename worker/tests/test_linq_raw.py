import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from worker.app.detector.linq_raw import load_raw_meta, pack, unpack

ROOT = Path(__file__).resolve().parents[2]


def _program(tmp_path, inputs, outputs):
    path = tmp_path / "toy.tpu"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("metadata.json", json.dumps({"hardware_parameters": {"ddr_word_len": 32},
                                                "inputs": {"1": inputs}, "outputs": {"2": outputs}}))
    return path


def _desc(user_shape, user_order, tpu_shape, tpu_order, dtype, scales, scale_shape, padding, size):
    return {"size": size, "user_shape": user_shape, "user_order": user_order, "tpu_shape": tpu_shape,
            "tpu_order": tpu_order, "tpu_dtype": dtype, "scales": scales, "scale_shape": scale_shape,
            "zero_points": [0.0] * len(scales), "padding": padding}


def test_pack_layout_like_yolo_input(tmp_path):
    # как у images: NCHW, 3 канала добиваются до 4, в tpu лежит HWC4
    prog = _program(tmp_path, {"images": _desc([1, 3, 4, 4], ["N", "C", "H", "W"], [1, 4, 4, 1, 4],
                                                ["C", "H", "W", "N", "B"], "int8", [127.0], [1],
                                                [[0, 0], [0, 1], [0, 0], [0, 0]], 64)}, {})
    hw, ins, _ = load_raw_meta(prog)
    x = np.random.default_rng(0).random((1, 3, 4, 4), dtype=np.float32)
    raw = pack(x, ins["images"], hw["ddr_word_len"])
    assert raw.dtype == np.uint8 and raw.shape == (2, 32)
    hwc = raw.view(np.int8).reshape(4, 4, 4)
    np.testing.assert_array_equal(hwc[..., :3], np.rint(x[0].transpose(1, 2, 0).astype(np.float64) * 127))
    assert not hwc[..., 3].any()


def test_unpack_crops_padding_and_scales(tmp_path):
    # как у Sigmoid: каналы 8400 -> группы по 128 с хвостом, на выходе scale по каналу
    n, groups, block = 10, 2, 8
    scales = list(np.linspace(0.01, 0.02, n))
    prog = _program(tmp_path, {}, {"cls": _desc([1, 3, n], ["N", "W", "C"], [groups, 3, 1, block], ["C", "W", "N", "B"],
                                                "int8", scales, [1, 1, n], [[0, 0], [0, 0], [0, -(groups * block - n)]],
                                                groups * 3 * block)})
    _, _, outs = load_raw_meta(prog)
    q = np.random.default_rng(1).integers(-128, 128, (1, 3, groups * block)).astype(np.int8)
    raw = q.reshape(1, 3, groups, block).transpose(2, 1, 0, 3).copy().view(np.uint8).reshape(-1, 16)
    y = unpack(raw, outs["cls"])
    np.testing.assert_allclose(y, q[..., :n] * np.asarray(scales, np.float32), rtol=1e-6)


# эталон снят sdk-шным tpu_io.pack/unpack в контейнере linq-sdk на настоящей программе ppe
@pytest.mark.skipif(not (ROOT / "data/linq/golden_ppe.npz").exists() or
                    not (ROOT / "models/ppe/linq/ppe_b1_o1_h1m.tpu").exists(), reason="нет эталона или программы")
def test_matches_sdk_packer():
    hw, ins, outs = load_raw_meta(ROOT / "models/ppe/linq/ppe_b1_o1_h1m.tpu")
    g = np.load(ROOT / "data/linq/golden_ppe.npz")
    for name, t in ins.items():
        np.testing.assert_array_equal(pack(g[f"in_x/{name}"], t, hw["ddr_word_len"]), g[f"in_raw/{name}"])
    for name, t in outs.items():
        np.testing.assert_array_equal(unpack(g[f"out_raw/{name}"], t), g[f"out_y/{name}"])


def test_image_packer_matches_float_path(tmp_path):
    from worker.app.detector.linq_raw import ImagePacker
    from worker.app.postprocess import letterbox, preprocess

    prog = _program(tmp_path, {"images": _desc([1, 3, 64, 64], ["N", "C", "H", "W"], [1, 64, 64, 1, 4],
                                                ["C", "H", "W", "N", "B"], "int8", [127.0], [1],
                                                [[0, 0], [0, 1], [0, 0], [0, 0]], 64 * 64 * 4)}, {})
    hw, ins, _ = load_raw_meta(prog)
    img = np.random.default_rng(2).integers(0, 256, (48, 80, 3), dtype=np.uint8)
    packer = ImagePacker(ins["images"], hw["ddr_word_len"])
    assert packer.fast
    x, _ = preprocess(img, 64)
    np.testing.assert_array_equal(packer(letterbox(img, 64)[0]), pack(x[None], ins["images"], hw["ddr_word_len"]))


def test_pack_cache_runs_once_per_image():
    import threading
    from worker.app.detector.linq import _PackCache

    cache, calls, img = _PackCache(), [], np.zeros((4, 4, 3), np.uint8)

    def make():
        calls.append(1)
        return "packed"

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get(("k", 1), img, make))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["packed"] * 4 and len(calls) == 1
    # другой кадр с тем же ключом (id переиспользован) пересчитывается
    other = np.zeros((4, 4, 3), np.uint8)
    assert cache.get(("k", 1), other, make) == "packed" and len(calls) == 2
