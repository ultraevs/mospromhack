from __future__ import annotations

import json
import sys
import types

import numpy as np
import pytest

from worker.app.detector.relay import (RelayDetector, dets_to_output, pack_reply, pack_request, relay_queue,
                                       unpack_request)
from worker.app.postprocess import decode, letterbox


# pika без сети: канал отвечает тем, что положили в replies (или молчит)
class FakeChannel:
    def __init__(self, broker):
        self.broker, self.is_open = broker, True

    def queue_declare(self, queue="", exclusive=False):
        return types.SimpleNamespace(method=types.SimpleNamespace(queue="amq.gen-1"))

    def basic_publish(self, exchange, routing_key, body, properties):
        self.broker.sent.append((routing_key, body, properties))

    def consume(self, queue, auto_ack=True, inactivity_timeout=None):
        while True:
            if self.broker.reply is not None:
                props = types.SimpleNamespace(correlation_id=self.broker.sent[-1][2].correlation_id)
                yield object(), props, self.broker.reply(self.broker.sent[-1])
            else:
                yield None, None, None

    def cancel(self):
        pass


@pytest.fixture
def broker(monkeypatch):
    state = types.SimpleNamespace(sent=[], reply=None)
    pika = types.ModuleType("pika")
    pika.PlainCredentials = lambda *a: None
    pika.ConnectionParameters = lambda *a, **k: None
    pika.BasicProperties = lambda **k: types.SimpleNamespace(**k)

    class Conn:
        def __init__(self, params):
            pass

        def channel(self):
            return FakeChannel(state)

        def close(self):
            pass

    pika.BlockingConnection = Conn
    monkeypatch.setitem(sys.modules, "pika", pika)
    return state


def test_request_roundtrip():
    img = np.random.default_rng(0).integers(0, 255, (640, 640, 3), dtype=np.uint8)
    body, headers = pack_request("ppe", [img, img[:, ::-1].copy()])
    assert len(body) < 640 * 640 * 3  # jpeg, не тензор
    model, images = unpack_request(body, {k: v.encode() for k, v in headers.items()})  # pika бывает отдаёт bytes
    assert model == "ppe" and len(images) == 2 and images[0].shape == img.shape


def test_reply_decodes_like_model_output(toy_spec):
    frame = np.zeros((720, 1280, 3), np.uint8)
    _, info = letterbox(frame, 640)
    reply = json.loads(pack_reply("x", [[[100, 200, 150, 300, 0.9, 0], [10, 20, 30, 40, 0.1, 1]]]))
    d = decode(dets_to_output(reply["dets"][0], toy_spec.nc), toy_spec, info)
    assert len(d) == 1  # 0.1 у beta ниже порога 0.2 — отрезано обычным decode
    assert d.cls[0] == 0 and d.conf[0] == pytest.approx(0.9)
    assert d.xyxy[0][0] == pytest.approx(200, abs=1)  # назад в пиксели кадра: масштаб 2


def test_queue_name_hides_login():
    assert "hackathon" not in relay_queue("hackathon123") and relay_queue("a") != relay_queue("b")


def test_infer_ok(broker, toy_spec):
    det = RelayDetector({"toy": toy_spec}, login="x", timeout_s=1)
    broker.reply = lambda sent: pack_reply(sent[2].correlation_id, [[[1, 2, 3, 4, 0.7, 2]]], {"ms": 5})
    out = det.infer("toy", det.prepare("toy", np.zeros((360, 640, 3), np.uint8))[0][None])
    assert out[0].shape == (4 + toy_spec.nc, 1) and out[0][4 + 2, 0] == pytest.approx(0.7)
    assert broker.sent[0][0] == relay_queue("x")


def test_timeout_gives_empty(broker, toy_spec):
    det = RelayDetector({"toy": toy_spec}, login="x", timeout_s=0.3)
    batch = np.stack([det.prepare("toy", np.zeros((360, 640, 3), np.uint8))[0]] * 2)
    out = det.infer("toy", batch)
    assert len(out) == 2 and all(o.shape == (4 + toy_spec.nc, 0) for o in out)
    assert det.timings["toy"]["lost"] == 2
    assert len(decode(out[0], toy_spec, letterbox(np.zeros((360, 640, 3), np.uint8), 640)[1])) == 0


def test_broker_down_gives_empty(broker, toy_spec, monkeypatch):
    det = RelayDetector({"toy": toy_spec}, login="x", timeout_s=0.3)

    def boom(*a, **k):
        raise ConnectionError("нет связи")

    monkeypatch.setattr(sys.modules["pika"], "BlockingConnection", boom)
    out = det.infer("toy", det.prepare("toy", np.zeros((64, 64, 3), np.uint8))[0][None])
    assert out[0].shape[1] == 0
