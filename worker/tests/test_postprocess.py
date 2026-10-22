import numpy as np
import pytest

from worker.app.postprocess import (PAD_VALUE, decode, iou_matrix, letterbox, nms, nms_per_class, preprocess,
                                    to_source)

A = 8400


def raw_output(spec, boxes):
    out = np.zeros((4 + spec.nc, A), dtype=np.float32)
    for k, (cls, cx, cy, w, h, conf) in enumerate(boxes):
        out[:4, k] = (cx, cy, w, h)
        out[4 + spec.class_id(cls), k] = conf
    return out


@pytest.mark.parametrize("w,h,scale,pad_x,pad_y", [
    (1920, 1080, 1 / 3, 0, 140),
    (1080, 1920, 1 / 3, 140, 0),
    (640, 640, 1.0, 0, 0),
    (320, 240, 2.0, 0, 80),
    (1280, 720, 0.5, 0, 140),
])
def test_letterbox_geometry(w, h, scale, pad_x, pad_y):
    img = np.full((h, w, 3), 200, dtype=np.uint8)
    out, info = letterbox(img, 640)
    assert out.shape == (640, 640, 3)
    assert info.scale == pytest.approx(scale)
    assert (info.pad_x, info.pad_y, info.src_w, info.src_h) == (pad_x, pad_y, w, h)
    new_w, new_h = round(w * scale), round(h * scale)
    assert (out[pad_y:pad_y + new_h, pad_x:pad_x + new_w] == 200).all()
    if pad_y:
        assert (out[:pad_y] == PAD_VALUE).all() and (out[pad_y + new_h:] == PAD_VALUE).all()
    if pad_x:
        assert (out[:, :pad_x] == PAD_VALUE).all() and (out[:, pad_x + new_w:] == PAD_VALUE).all()


def test_letterbox_odd_padding_matches_ultralytics_rounding():
    out, info = letterbox(np.zeros((480, 641, 3), np.uint8), 640)
    assert info.pad_y == 80 and round(480 * info.scale) == 479
    assert (out[-1] == PAD_VALUE).all()


def test_preprocess_layout_color_and_scale():
    img = np.zeros((640, 640, 3), np.uint8)
    img[..., 0] = 255
    chw, _ = preprocess(img, 640)
    assert chw.shape == (3, 640, 640) and chw.dtype == np.float32
    assert chw[2].min() == 1.0
    assert chw[0].max() == 0.0 and chw[1].max() == 0.0
    assert chw.flags["C_CONTIGUOUS"]


def test_to_source_roundtrip():
    _, info = letterbox(np.zeros((1080, 1920, 3), np.uint8), 640)
    src = np.array([[100, 50, 400, 900], [0, 0, 1920, 1080], [1500.5, 300.25, 1700, 620]], np.float32)
    model = src.copy()
    model[:, [0, 2]] = src[:, [0, 2]] * info.scale + info.pad_x
    model[:, [1, 3]] = src[:, [1, 3]] * info.scale + info.pad_y
    np.testing.assert_allclose(to_source(model, info), src, atol=1e-3)


def test_to_source_clips_to_frame():
    _, info = letterbox(np.zeros((1080, 1920, 3), np.uint8), 640)
    out = to_source(np.array([[600, 100, 700, 300]], np.float32), info)
    assert out[0, 1] == 0 and out[0, 2] == 1920
    assert out[0, 3] == pytest.approx((300 - 140) * 3)


def test_iou_matrix():
    a = np.array([[0, 0, 10, 10]], np.float32)
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30], [0, 0, 0, 0]], np.float32)
    np.testing.assert_allclose(iou_matrix(a, b)[0], [1.0, 1 / 3, 0.0, 0.0], atol=1e-6)


def test_nms_keeps_best_of_overlapping_and_all_separate():
    boxes = np.array([[0, 0, 10, 10], [1, 0, 11, 10], [50, 50, 60, 60], [0, 0, 10, 9]], np.float32)
    scores = np.array([0.6, 0.9, 0.5, 0.7], np.float32)
    assert nms(boxes, scores, 0.5).tolist() == [1, 2]


def test_nms_threshold_boundary():
    boxes = np.array([[0, 0, 10, 10], [5, 0, 15, 10]], np.float32)
    scores = np.array([0.9, 0.8], np.float32)
    assert nms(boxes, scores, 0.3).tolist() == [0]
    assert nms(boxes, scores, 0.4).tolist() == [0, 1]


def test_nms_empty():
    assert nms(np.zeros((0, 4), np.float32), np.zeros(0, np.float32), 0.5).size == 0
    assert nms_per_class(np.zeros((0, 4), np.float32), np.zeros(0, np.float32), np.zeros(0, np.int64), 0.5).size == 0


def test_nms_per_class_does_not_suppress_across_classes():
    boxes = np.array([[0, 0, 10, 10], [0, 0, 10, 10], [0, 0, 10, 10]], np.float32)
    scores = np.array([0.9, 0.8, 0.7], np.float32)
    cls = np.array([0, 1, 0])
    assert nms_per_class(boxes, scores, cls, 0.5).tolist() == [0, 1]


def test_decode_applies_per_class_thresholds(toy_spec):
    _, info = letterbox(np.zeros((640, 640, 3), np.uint8), 640)
    out = raw_output(toy_spec, [
        ("alpha", 100, 100, 40, 40, 0.49),
        ("alpha", 300, 100, 40, 40, 0.51),
        ("beta", 100, 300, 40, 40, 0.21),
        ("gamma", 300, 300, 40, 40, 0.79),
        ("gamma", 500, 300, 40, 40, 0.80),
    ])
    det = decode(out, toy_spec, info)
    got = sorted((toy_spec.classes[c], round(float(s), 2)) for c, s in zip(det.cls, det.conf))
    assert got == [("alpha", 0.51), ("beta", 0.21), ("gamma", 0.80)]


def test_decode_box_format_and_order(toy_spec):
    _, info = letterbox(np.zeros((640, 640, 3), np.uint8), 640)
    det = decode(raw_output(toy_spec, [("beta", 100, 200, 40, 60, 0.3), ("alpha", 400, 400, 20, 20, 0.9)]),
                 toy_spec, info)
    assert det.conf.tolist() == pytest.approx([0.9, 0.3])
    np.testing.assert_allclose(det.xyxy[1], [80, 170, 120, 230])
    assert det.xyxy.dtype == np.float32 and det.cls.dtype == np.int64


def test_decode_maps_back_to_source_frame(toy_spec):
    _, info = letterbox(np.zeros((1080, 1920, 3), np.uint8), 640)
    det = decode(raw_output(toy_spec, [("alpha", 320, 320, 100, 50, 0.9)]), toy_spec, info)
    np.testing.assert_allclose(det.xyxy[0], [810, 465, 1110, 615], atol=1e-3)


def test_decode_nms_within_class_only(toy_spec):
    _, info = letterbox(np.zeros((640, 640, 3), np.uint8), 640)
    out = raw_output(toy_spec, [
        ("alpha", 100, 100, 40, 40, 0.9),
        ("alpha", 102, 100, 40, 40, 0.7),
        ("beta", 100, 100, 40, 40, 0.6),
    ])
    det = decode(out, toy_spec, info)
    assert sorted(toy_spec.classes[c] for c in det.cls) == ["alpha", "beta"]
    assert det.conf.max() == pytest.approx(0.9)


def test_decode_takes_best_class_per_anchor(toy_spec):
    _, info = letterbox(np.zeros((640, 640, 3), np.uint8), 640)
    out = raw_output(toy_spec, [("alpha", 100, 100, 40, 40, 0.6)])
    out[4 + toy_spec.class_id("beta"), 0] = 0.55
    det = decode(out, toy_spec, info)
    assert [toy_spec.classes[c] for c in det.cls] == ["alpha"]


def test_decode_empty_and_wrong_shape(toy_spec):
    _, info = letterbox(np.zeros((640, 640, 3), np.uint8), 640)
    det = decode(np.zeros((4 + toy_spec.nc, A), np.float32), toy_spec, info)
    assert len(det) == 0 and det.xyxy.shape == (0, 4)
    with pytest.raises(ValueError, match="ожидалось"):
        decode(np.zeros((11, A), np.float32), toy_spec, info)
