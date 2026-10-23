import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parents[2] / "scripts" / "linq_bridge.py"
_spec = importlib.util.spec_from_file_location("linq_bridge", _p)
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)


def test_request_roundtrip():
    h, b = lb.encode_request("post", "/internal/events?x=1",
                             [("Content-Type", "multipart/form-data; boundary=zz"), ("X-Internal-Token", "t"),
                              ("Host", "127.0.0.1:8000"), ("Content-Length", "3")], b"\x00\xff1")
    method, path, hdrs, body = lb.decode_request(h, b)
    assert (method, path, body) == ("POST", "/internal/events?x=1", b"\x00\xff1")
    assert dict(hdrs) == {"Content-Type": "multipart/form-data; boundary=zz", "X-Internal-Token": "t"}


def test_response_roundtrip_and_bytes_headers():
    h, b = lb.encode_response(201, {"content-type": "application/json", "content-encoding": "gzip"}, b"{}")
    h = {k: (v.encode() if isinstance(v, str) else v) for k, v in h.items()}  # pika может отдать bytes
    status, hdrs, body = lb.decode_response(h, b)
    assert status == 201 and body == b"{}" and hdrs == [["content-type", "application/json"]]


def test_rewrite_config():
    cfg = {"version": 3, "cameras": [
        {"id": 1, "rtsp_url": "rtsp://h:8554/cam-1"},
        {"id": 2, "rtsp_url": "rtsp://h:8554/cam-2"},
        {"id": 3, "rtsp_url": "rtsp://h:8554/cam-3"},
    ]}
    src = lb.sources_from_cameras([
        {"id": 1, "source_type": "file", "source": "sub/site.mp4"},
        {"id": 2, "source_type": "rtsp", "source": "rtsp://cam/1"},
    ])
    out = lb.rewrite_config(cfg, src)
    assert [c["rtsp_url"] for c in out["cameras"]] == ["syncfile://site.mp4", "rtsp://h:8554/cam-2",
                                                       "rtsp://h:8554/cam-3"]
    assert lb.file_name("a\\b\\c.mp4") == "c.mp4"


def test_ws_url():
    assert lb.ws_url("http://127.0.0.1:8000/") == "ws://127.0.0.1:8000/internal/detections"
