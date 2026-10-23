import dataclasses
import sys

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


def test_linq_detector_without_runtime(toy_spec, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pytpu", None)
    with pytest.raises(RuntimeError, match="pytpu"):
        create_detector("linq", {"toy": _toy_linq_spec(toy_spec, tmp_path)}, linq={"transport": "pytpu"})


class _FakeTpu:

    def __init__(self, output_name: str, nc: int):
        self.output_name, self.nc, self.calls, self.closed = output_name, nc, 0, 0
        fake = self

        class _Ctx:
            def __init__(self, value):
                self.value = value

            def __enter__(self):
                return self.value

            def __exit__(self, *exc):
                fake.closed += 1

        class _Inference:
            def sync(self, inputs):
                fake.calls += 1
                (x,) = inputs.values()
                assert x.shape == (1, 3, 640, 640)
                return {fake.output_name: np.full((1, 4 + fake.nc, 8400), fake.calls, np.float32)}

        class _Program:
            def inference(self):
                return _Ctx(_Inference())

        class _Device:
            def load(self, path):
                assert path.endswith(".tpu")
                return _Ctx(_Program())

            @staticmethod
            def list_devices():
                return ["/dev/tpu0"]

            @staticmethod
            def open(name):
                return _Ctx(_Device())

        self.Device = _Device


def test_linq_detector_runs_program_from_meta(toy_spec, tmp_path, monkeypatch):
    model_dir = tmp_path / "toy"
    (model_dir / "linq").mkdir(parents=True)
    spec = dataclasses.replace(toy_spec, onnx_path=model_dir / "best.onnx")
    (model_dir / "linq" / "toy_b1_o1_h1m.tpu").write_bytes(b"")
    (model_dir / "linq" / "meta.yaml").write_text(
        "program: toy_b1_o1_h1m.tpu\npreset: o1\ninput: images\ntpu_outputs: [output0]\ntail_onnx: null\n",
        encoding="utf-8")
    fake = _FakeTpu("output0", spec.nc)
    monkeypatch.setitem(sys.modules, "pytpu", fake)

    det = create_detector("linq", {"toy": spec})
    assert isinstance(det, LinqDetector) and det.batch_limit("toy") == 1
    out = det.infer("toy", np.zeros((3, 3, 640, 640), np.float32))
    assert out.shape == (3, 4 + spec.nc, 8400) and out.dtype == np.float32
    assert [float(o[0, 0]) for o in out] == [1, 2, 3]
    assert det.timings["toy"]["frames"] == 3
    det.close()
    assert fake.closed == 3


def test_yolo_tail_matches_head_layout():
    from worker.app.detector.linq import yolo_tail

    xy = np.array([[[1.0, 2.0], [3.0, 4.0]]], np.float32)
    wh = np.array([[[5.0, 6.0], [7.0, 8.0]]], np.float32)
    cls = np.array([[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]], np.float32)
    strides = np.array([[8.0, 16.0]], np.float32)
    out = yolo_tail({"a": xy, "b": wh, "c": cls}, ["a", "b", "c"], strides)
    assert out.shape == (1, 7, 2)
    np.testing.assert_allclose(out[0, :4], [[8, 32], [24, 64], [40, 96], [56, 128]])
    np.testing.assert_allclose(out[0, 4:], cls[0])


def test_linq_detector_without_compiled_program(toy_spec, tmp_path, monkeypatch):
    spec = dataclasses.replace(toy_spec, onnx_path=tmp_path / "best.onnx")
    monkeypatch.setitem(sys.modules, "pytpu", _FakeTpu("output0", spec.nc))
    with pytest.raises(FileNotFoundError, match="linq_compile.py"):
        create_detector("linq", {"toy": spec})


def _fake_cloud(monkeypatch, nc):
    import pickle
    import types

    published = []

    class _Channel:
        is_open = True

        def queue_declare(self, **kw): pass
        def queue_bind(self, **kw): pass
        def cancel(self): pass

        def basic_publish(self, exchange, routing_key, body, properties):
            published.append((routing_key, pickle.loads(body), properties.headers))

        def consume(self, queue, auto_ack, inactivity_timeout):
            for i, (_, _, headers) in reversed(list(enumerate(published))):
                out = {"output0": np.full((1, 4 + nc, 8400), i + 1, np.float32)}
                yield None, None, None
                yield object(), types.SimpleNamespace(headers=dict(headers)), pickle.dumps(out)

    class _Conn:
        def __init__(self, params): self.params = params
        def channel(self): return _Channel()
        def close(self): pass

    pika = types.SimpleNamespace(
        ConnectionParameters=lambda *a, **kw: (a, kw), PlainCredentials=lambda *a: a,
        BasicProperties=lambda headers: types.SimpleNamespace(headers=headers), BlockingConnection=_Conn)

    class PyTask:
        def __init__(self, data): self.data = data

    fmt = types.SimpleNamespace(
        PyTask=PyTask, serialize=lambda t: pickle.dumps(t.data),
        deserialize=lambda b: (pickle.loads(b), (), {"worker_pid": "1", "status_code": 0, "error_message": "",
                                                     "inference_time": 0.002}),
        TARGET_URI_ATTR_NAME="uri", RESPONSE_ROUTING_KEYS_ATTR_NAME="response_routing_keys",
        MESSAGE_ID_ATTR_NAME="tpu_pycloud_message_id")
    monkeypatch.setitem(sys.modules, "pika", pika)
    monkeypatch.setitem(sys.modules, "tpu_pycloud_format", fmt)
    return published


def _toy_linq_spec(toy_spec, tmp_path):
    model_dir = tmp_path / "toy"
    (model_dir / "linq").mkdir(parents=True)
    (model_dir / "linq" / "toy_b1_o1_h1m.tpu").write_bytes(b"")
    (model_dir / "linq" / "meta.yaml").write_text(
        "program: toy_b1_o1_h1m.tpu\npreset: o1\ninput: images\ntpu_outputs: [output0]\ntail_onnx: null\n",
        encoding="utf-8")
    return dataclasses.replace(toy_spec, onnx_path=model_dir / "best.onnx")


def test_linq_detector_cloud_transport(toy_spec, tmp_path, monkeypatch):
    spec = _toy_linq_spec(toy_spec, tmp_path)
    published = _fake_cloud(monkeypatch, spec.nc)

    det = LinqDetector({"toy": spec}, transport="cloud", program_dir="/auto/cache/u")
    assert det.batch_limit("toy") == 8 and "cloud" in det.device
    out = det.infer("toy", np.zeros((3, 3, 640, 640), np.float32))
    assert out.shape == (3, 4 + spec.nc, 8400)
    assert [float(o[0, 0]) for o in out] == [1, 2, 3]
    assert len(published) == 3
    for routing_key, _, headers in published:
        assert routing_key == "input"
        assert headers["uri"] == "file:///auto/cache/u/toy_b1_o1_h1m.tpu?raw=False"
    assert det.timings["toy"]["tpu_ms"] == pytest.approx(3 * 2.0)
    det.close()


def test_linq_detector_cloud_needs_login(toy_spec, tmp_path, monkeypatch):
    spec = _toy_linq_spec(toy_spec, tmp_path)
    _fake_cloud(monkeypatch, spec.nc)
    with pytest.raises(RuntimeError, match="LINQ_LOGIN"):
        LinqDetector({"toy": spec}, transport="cloud", program_dir="")


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
