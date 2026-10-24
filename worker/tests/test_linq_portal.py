import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "scripts" / "linq_portal.py"


def load():
    spec = importlib.util.spec_from_file_location("linq_portal", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_map():
    m = load()
    assert m.parse_map("5173:5174") == (5173, 5174)
    assert m.parse_map("8000") == (8000, 8000)


def test_remote_path():
    m = load()
    assert m.remote_path("u", 8000, "/api/x", "a=1&b=2") == "/user/u/proxy/8000/api/x?a=1&b=2"
    assert m.remote_path("u", 5173, "/") == "/user/u/proxy/5173/"
    assert m.remote_path("u", 5173, "x") == "/user/u/proxy/5173/x"


def test_rewrite_location():
    m = load()
    h = "hackathon.lin-q.ru"
    assert m.rewrite_location("/user/u/proxy/8000/docs?x=1", "u", 8000, h) == "/docs?x=1"
    assert m.rewrite_location(f"http://{h}/user/u/proxy/8000/a/b", "u", 8000, h) == "/a/b"
    assert m.rewrite_location("/user/u/proxy/8000", "u", 8000, h) == "/"
    assert m.rewrite_location("/user/u/proxy/80001/x", "u", 8000, h) == "/user/u/proxy/80001/x"
    assert m.rewrite_location("https://other.example/x", "u", 8000, h) == "https://other.example/x"
    assert m.rewrite_location("/relative", "u", 8000, h) == "/relative"


def test_filter_headers():
    m = load()
    headers = {"Host": "localhost:8000", "Connection": "keep-alive", "Transfer-Encoding": "chunked",
               "Accept": "*/*", "Referer": "http://localhost:5173/", "Range": "bytes=0-"}
    out = m.filter_headers(headers, m.REQ_DROP)
    assert out == {"Accept": "*/*", "Range": "bytes=0-"}
    resp = m.filter_headers({"Content-Length": "10", "Set-Cookie": "a=b", "Content-Type": "video/mp2t"}, m.RESP_DROP)
    assert resp == {"Content-Length": "10", "Content-Type": "video/mp2t"}


def test_expired():
    m = load()
    assert m.expired(403, "")
    assert m.expired(302, "/hub/login?next=/user/u/proxy/8000/")
    assert not m.expired(302, "/docs")
    assert not m.expired(503, "")
