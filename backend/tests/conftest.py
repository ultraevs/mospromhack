from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from shared.config import ROOT, Settings

TOKEN = "test-token"
INTERNAL = {"X-Internal-Token": TOKEN}


@pytest.fixture
def settings(tmp_path) -> Settings:
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "demo.mp4").write_bytes(b"not a real video")
    return Settings(_env_file=None, internal_token=TOKEN, telegram_bot_token="", auth_enabled=False, data_dir=tmp_path,
                    config_path=ROOT / "config.yaml")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, run_sources=False)) as c:  # без ffmpeg и mediamtx
        yield c


@pytest.fixture
def camera(client) -> dict:
    r = client.post("/api/cameras", json={"name": "Цех 2, ворота", "source_type": "file", "source": "demo.mp4"})
    assert r.status_code == 201, r.text
    return r.json()


def event_body(camera_id: int, **over) -> dict:
    body = {"type": "no_helmet", "severity": "medium", "camera_id": camera_id, "track_id": 17,
            "started_at": "2026-10-04T12:31:05.120Z", "confidence": 0.62, "bbox": [0.41, 0.22, 0.08, 0.31],
            "details": {"fraction": 0.58, "window_s": 4, "observations": 19}}
    body.update(over)
    return body
