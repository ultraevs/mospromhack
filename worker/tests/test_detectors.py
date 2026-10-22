import numpy as np
import pytest

from worker.app.detector import create_detector
from worker.app.detector.fake import FakeBox, FakeDetector
from worker.app.detector.linq import LinqDetector
from worker.app.postprocess import decode, letterbox

BATCH = np.zeros((1, 3, 640, 640), np.float32)


def test_fake_detector_output_decodes_back(toy_spec):
    det = FakeDetector({"toy": toy_spec}, {"toy": [[FakeBox("alpha", (0.25, 0.25, 0.5, 0.25), 0.9),
                                                    FakeBox("beta", (0.0, 0.5, 0.1, 0.1), 0.4)]]})
    out = det.infer("toy", BATCH)
    assert out.shape == (1, 4 + toy_spec.nc, 8400) and out.dtype == np.float32
    _, info = letterbox(np.zeros((640, 640, 3), np.uint8), 640)
    d = decode(out[0], toy_spec, info)
    assert [toy_spec.classes[c] for c in d.cls] == ["alpha", "beta"]
    np.testing.assert_allclose(d.xyxy, [[160, 160, 480, 320], [0, 320, 64, 384]], atol=1e-3)


def test_fake_detector_steps_loop_and_stop(toy_spec):
    steps = {"toy": [[FakeBox("alpha", (0, 0, 0.1, 0.1))], []]}
    looped = FakeDetector({"toy": toy_spec}, steps)
    counts = [int((looped.infer("toy", BATCH)[0, 4:] > 0).sum()) for _ in range(4)]
    assert counts == [1, 0, 1, 0]
    once = FakeDetector({"toy": toy_spec}, steps, loop=False)
    assert [int((once.infer("toy", BATCH)[0, 4:] > 0).sum()) for _ in range(4)] == [1, 0, 0, 0]


def test_fake_detector_callable_scenario_sees_batch_item(toy_spec):
    det = FakeDetector({"toy": toy_spec}, lambda model, call, item: [FakeBox("gamma", (0, 0, 0.1, 0.1))] * item)
    out = det.infer("toy", np.zeros((3, 3, 640, 640), np.float32))
    assert [int((o[4:] > 0).sum()) for o in out] == [0, 1, 2]


def test_fake_detector_from_yaml(toy_spec, tmp_path):
    path = tmp_path / "scenario.yaml"
    path.write_text("loop: false\ntoy:\n  - [{cls: beta, bbox: [0.1, 0.1, 0.2, 0.2], conf: 0.7}]\n", encoding="utf-8")
    det = create_detector("fake", {"toy": toy_spec}, fake_scenario=path)
    out = det.infer("toy", BATCH)
    assert out[0, 4 + toy_spec.class_id("beta"), 0] == pytest.approx(0.7)
    assert not det.infer("toy", BATCH).any()


def test_fake_detector_unknown_class(toy_spec):
    det = FakeDetector({"toy": toy_spec}, {"toy": [[FakeBox("helmet", (0, 0, 0.1, 0.1))]]})
    with pytest.raises(KeyError):
        det.infer("toy", BATCH)


def test_linq_detector_is_a_stub(toy_spec):
    det = create_detector("linq", {"toy": toy_spec})
    assert isinstance(det, LinqDetector)
    with pytest.raises(NotImplementedError, match="LINQ.md"):
        det.infer("toy", BATCH)


def test_unknown_detector_kind(toy_spec):
    with pytest.raises(ValueError, match="неизвестный детектор"):
        create_detector("tensorrt", {"toy": toy_spec})


@pytest.mark.models
def test_onnx_detector_on_cpu(real_specs):
    from worker.app.detector.onnx_detector import OnnxDetector

    det = OnnxDetector(real_specs, allow_cuda=False)
    assert det.device == "cpu" and det.warnings == []
    for name, spec in real_specs.items():
        out = det.infer(name, np.zeros((2, 3, 640, 640), np.float32))
        assert out.shape == (2, 4 + spec.nc, 8400) and out.dtype == np.float32


@pytest.mark.gpu
def test_onnx_detector_really_uses_cuda(real_specs):
    from worker.app.detector.onnx_detector import OnnxDetector

    gpu = OnnxDetector(real_specs)
    assert gpu.device == "cuda", gpu.warnings
    cpu = OnnxDetector(real_specs, allow_cuda=False)
    batch = np.random.default_rng(0).random((1, 3, 640, 640), dtype=np.float32)
    for name in real_specs:
        np.testing.assert_allclose(gpu.infer(name, batch), cpu.infer(name, batch), atol=0.5, rtol=0.02)


@pytest.mark.models
def test_dynamic_onnx_matches_static(real_specs):
    from worker.app.detector.onnx_detector import DYNAMIC_NAME, OnnxDetector

    if not all(s.onnx_path.with_name(DYNAMIC_NAME).exists() for s in real_specs.values()):
        pytest.skip("нет best.dynamic.onnx; запусти scripts/export_dynamic.py")
    static = OnnxDetector(real_specs, allow_cuda=False, prefer_dynamic=False)
    dynamic = OnnxDetector(real_specs, allow_cuda=False)
    rng = np.random.default_rng(1)
    batch = np.clip(rng.random((13, 3, 20, 20), dtype=np.float32).repeat(32, axis=2).repeat(32, axis=3), 0, 1)
    for name in real_specs:
        assert static.batch_limit(name) == 1 and dynamic.batch_limit(name) == 8
        a, b = static.infer(name, batch), dynamic.infer(name, batch)
        assert a.shape == b.shape == (13, 4 + real_specs[name].nc, 8400)
        np.testing.assert_allclose(a, b, atol=1e-2, rtol=1e-3)
        assert dynamic.infer(name, batch[:1]).shape == (1, 4 + real_specs[name].nc, 8400)
        assert dynamic.infer(name, batch[:4]).shape == (4, 4 + real_specs[name].nc, 8400)


@pytest.mark.gpu
def test_fp16_file_is_used_for_batches_and_matches_fp32(real_specs):
    from worker.app.detector.onnx_detector import FP16_NAME, OnnxDetector

    if not all(s.onnx_path.with_name(FP16_NAME).exists() for s in real_specs.values()):
        pytest.skip("нет best.fp16.onnx; запусти scripts/make_fp16.py")
    full = OnnxDetector(real_specs, precision="fp32")
    half = OnnxDetector(real_specs, precision="auto")
    if half.device != "cuda":
        pytest.skip("нет видеокарты")
    rng = np.random.default_rng(1)
    batch = np.clip(rng.random((8, 3, 20, 20), dtype=np.float32).repeat(32, axis=2).repeat(32, axis=3), 0, 1)
    for name in real_specs:
        assert full.precision[name] == "fp32" and half.precision[name] == "fp16"
        a, b = full.infer(name, batch), half.infer(name, batch)
        assert a.shape == b.shape and b.dtype == np.float32
        np.testing.assert_allclose(a[:, 4:], b[:, 4:], atol=0.02)
        diff = np.abs(a[:, :4] - b[:, :4])
        assert np.quantile(diff, 0.999) < 2.0 and diff.max() < 8.0


@pytest.mark.models
def test_fp16_is_not_used_on_cpu(real_specs):
    from worker.app.detector.onnx_detector import OnnxDetector

    detector = OnnxDetector(real_specs, allow_cuda=False, precision="auto")
    assert set(detector.precision.values()) == {"fp32"}
