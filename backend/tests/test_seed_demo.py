from __future__ import annotations

import random

from sqlalchemy import select

from backend.app.db.models import CameraRow, EventRow
from backend.app.db.session import make_engine, make_session_factory
from backend.tests.conftest import event_body
from scripts import seed_demo


def test_seed_and_clear(client, camera, settings):
    assert client.post("/internal/events", json=event_body(camera["id"]),
                       headers={"X-Internal-Token": "test-token"}).status_code in (200, 201)
    factory = make_session_factory(make_engine(settings.data_dir / "db.sqlite"))
    with factory() as db:
        n = seed_demo.seed(db, 7, rnd=random.Random(1))
        assert n > 20
        seed_demo.seed(db, 1, rnd=random.Random(2))  # повтор не плодит камеры
        demo_cams = db.execute(select(CameraRow).where(CameraRow.name.endswith(" (демо)"))).scalars().all()
        assert len(demo_cams) == len(seed_demo.CAMERAS)
        assert not any(c.enabled for c in demo_cams)

        events, cams = seed_demo.clear_demo(db)
        assert events >= n and cams == len(seed_demo.CAMERAS)
        left = db.execute(select(EventRow)).scalars().all()
        assert len(left) == 1 and left[0].camera_id == camera["id"]
        assert db.get(CameraRow, camera["id"]) is not None

        assert seed_demo.clear_live(db) == 1
