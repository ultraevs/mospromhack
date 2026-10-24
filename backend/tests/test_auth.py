import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.main import create_app
from backend.tests.conftest import INTERNAL, TOKEN, event_body
from shared.config import ROOT, Settings

RTSP = "rtsp://cam:secret@10.0.0.5:554/stream1"


@pytest.fixture
def app(tmp_path):
    (tmp_path / "videos").mkdir()
    settings = Settings(_env_file=None, internal_token=TOKEN, telegram_bot_token="", auth_enabled=True, data_dir=tmp_path,
                        config_path=ROOT / "config.yaml", auth_admin_login="admin", auth_admin_password="admin-pass",
                        auth_viewer_login="viewer", auth_viewer_password="viewer-pass")
    return create_app(settings, run_sources=False)


def login(c, who):
    r = c.post("/api/auth/login", json={"login": who, "password": f"{who}-pass"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin(app):
    with TestClient(app) as c:  # он держит lifespan, остальные клиенты без него
        yield login(c, "admin")


@pytest.fixture
def viewer(app, admin):
    return login(TestClient(app), "viewer")


def test_login_me_logout(admin):
    assert admin.post("/api/auth/login", json={"login": "admin", "password": "nope"}).status_code == 401
    me = admin.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "admin"
    assert admin.post("/api/auth/logout").status_code == 204
    assert admin.get("/api/auth/me").status_code == 401
    assert admin.get("/api/cameras").status_code == 401


def test_viewer_permissions(admin, viewer):
    cam = admin.post("/api/cameras", json={"name": "Ворота", "source_type": "rtsp", "source": RTSP}).json()
    ev = admin.post("/internal/events", headers=INTERNAL, json=event_body(cam["id"])).json()

    assert viewer.get("/api/events").status_code == 200
    assert viewer.get(f"/api/events/{ev['id']}").status_code == 200
    assert viewer.patch(f"/api/events/{ev['id']}", json={"review": "confirmed"}).status_code == 200
    assert viewer.post("/api/cameras", json={"name": "x", "source_type": "rtsp", "source": RTSP}).status_code == 403
    assert viewer.get("/api/settings/rules").status_code == 403
    assert viewer.get("/api/auth/users").status_code == 403

    assert viewer.get("/api/cameras").json()[0]["source"] == "rtsp://***@10.0.0.5:554/stream1"
    assert viewer.get(f"/api/cameras/{cam['id']}").json()["source"] == "rtsp://***@10.0.0.5:554/stream1"
    assert admin.get("/api/cameras").json()[0]["source"] == RTSP


def test_internal_token_is_admin(app, admin):
    c = TestClient(app)
    assert c.get("/api/cameras").status_code == 401
    assert c.get("/api/cameras", headers={"X-Internal-Token": "wrong"}).status_code == 401
    r = c.post("/api/cameras", headers=INTERNAL, json={"name": "Ворота", "source_type": "rtsp", "source": RTSP})
    assert r.status_code == 201
    assert c.get("/api/cameras", headers=INTERNAL).json()[0]["source"] == RTSP


def test_admin_cannot_lock_self_out(admin):
    me = admin.get("/api/auth/me").json()
    assert admin.patch(f"/api/auth/users/{me['id']}", json={"active": False}).status_code == 400
    assert admin.patch(f"/api/auth/users/{me['id']}", json={"role": "viewer"}).status_code == 400
    other = admin.post("/api/auth/users", json={"login": "boss2", "display_name": "Второй", "role": "admin",
                                                "password": "boss2-pass"}).json()
    assert admin.patch(f"/api/auth/users/{other['id']}", json={"role": "viewer"}).status_code == 200


def test_last_admin_cannot_be_demoted(admin):
    users = admin.get("/api/auth/users").json()
    me = admin.get("/api/auth/me").json()
    r = TestClient(admin.app).patch(f"/api/auth/users/{me['id']}", headers=INTERNAL, json={"role": "viewer"})
    assert r.status_code == 400
    assert [u for u in users if u["role"] == "admin"] == [me]


def test_ws_events_needs_session(app, viewer):
    with pytest.raises(WebSocketDisconnect) as e:
        with TestClient(app).websocket_connect("/ws/events") as ws:
            ws.receive_text()
    assert e.value.code == 4401
    with viewer.websocket_connect("/ws/events"):
        pass
