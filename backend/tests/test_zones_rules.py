from backend.tests.conftest import INTERNAL

SQUARE = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]


def config(client) -> dict:
    return client.get("/internal/config", headers=INTERNAL).json()


def test_zone_crud(client, camera):
    base = f"/api/cameras/{camera['id']}/zones"
    assert client.get(base).json() == []

    r = client.post(base, json={"name": "Зона резки", "type": "ppe_required", "polygon": SQUARE,
                                "required_ppe": ["helmet", "mask"]})
    assert r.status_code == 201
    zone = r.json()
    assert zone == {"id": zone["id"], "camera_id": camera["id"], "name": "Зона резки", "type": "ppe_required",
                    "polygon": SQUARE, "required_ppe": ["helmet", "mask"]}
    assert client.get(base).json() == [zone]

    moved = [[0.1, 0.1], [0.5, 0.1], [0.3, 0.6]]
    r = client.patch(f"/api/zones/{zone['id']}", json={"polygon": moved, "type": "restricted", "required_ppe": []})
    assert r.status_code == 200
    assert (r.json()["polygon"], r.json()["type"], r.json()["required_ppe"], r.json()["name"]) == (
        moved, "restricted", [], "Зона резки")

    assert client.delete(f"/api/zones/{zone['id']}").status_code == 204
    assert client.get(base).json() == []
    assert client.delete(f"/api/zones/{zone['id']}").status_code == 404
    assert client.patch(f"/api/zones/{zone['id']}", json={"name": "x"}).status_code == 404


def test_zone_validation(client, camera):
    base = f"/api/cameras/{camera['id']}/zones"
    bad = [
        {"name": "a", "type": "restricted", "polygon": [[0.1, 0.1], [0.5, 0.5]]},
        {"name": "a", "type": "restricted", "polygon": [[0.1, 0.1], [0.5, 0.5], [1.2, 0.3]]},
        {"name": "a", "type": "restricted", "polygon": [[0.1, 0.1], [0.5, 0.5], [0.3]]},
        {"name": "a", "type": "danger", "polygon": SQUARE},
        {"name": "a", "type": "ppe_required", "polygon": SQUARE, "required_ppe": ["gloves"]},
    ]
    for body in bad:
        assert client.post(base, json=body).status_code == 422, body
    assert client.post("/api/cameras/999/zones", json={"name": "a", "type": "restricted", "polygon": SQUARE}).status_code == 404
    assert client.get("/api/cameras/999/zones").status_code == 404

    zone = client.post(base, json={"name": "a", "type": "restricted", "polygon": SQUARE}).json()
    assert client.patch(f"/api/zones/{zone['id']}", json={"polygon": [[0, 0], [2, 0], [0, 1]]}).status_code == 422
    assert client.patch(f"/api/zones/{zone['id']}", json={"polygon": [[0, 0], [1, 0]]}).status_code == 422


def test_zone_changes_reach_worker_config_with_new_version(client, camera):
    v0 = config(client)["version"]
    zone = client.post(f"/api/cameras/{camera['id']}/zones",
                       json={"name": "Запретная", "type": "restricted", "polygon": SQUARE}).json()
    cfg = config(client)
    assert cfg["version"] == v0 + 1
    assert cfg["cameras"][0]["zones"] == [zone]

    client.patch(f"/api/zones/{zone['id']}", json={"name": "Склад ГСМ", "type": "flammable"})
    cfg = config(client)
    assert cfg["version"] == v0 + 2 and cfg["cameras"][0]["zones"][0]["type"] == "flammable"

    client.delete(f"/api/zones/{zone['id']}")
    cfg = config(client)
    assert cfg["version"] == v0 + 3 and cfg["cameras"][0]["zones"] == []


def test_deleting_camera_deletes_its_zones(client, camera):
    zone = client.post(f"/api/cameras/{camera['id']}/zones",
                       json={"name": "a", "type": "restricted", "polygon": SQUARE}).json()
    client.delete(f"/api/cameras/{camera['id']}")
    assert client.patch(f"/api/zones/{zone['id']}", json={"name": "b"}).status_code == 404


def test_rules_defaults_come_from_config_yaml(client):
    rules = client.get("/api/settings/rules").json()
    assert rules["no_helmet"] == {"window_s": 4, "min_observations": 12, "fraction": 0.6}
    assert rules["no_mask"] == {"window_s": 4, "min_observations": 12, "fraction": 0.6}
    assert rules["no_vest"] == {"window_s": 6, "min_observations": 15, "max_fraction": 0.1, "min_person_height_px": 120}
    assert rules["zone_intrusion"] == {"window_s": 1.5, "fraction": 0.7, "min_observations": 4}
    assert rules["smoking"] == {"k": 3, "m": 5, "upper_fraction": 0.33, "everywhere": False}
    assert rules["fire"] == rules["smoke"] == {"k": 3, "m": 5}
    assert rules["lifecycle"] == {"close_after_s": 5, "cooldown_s": 60, "retrack_iou": 0.3, "retrack_window_s": 120,
                                  "camera_cooldown_s": 60, "track_lost_s": 2}


def test_rules_update_is_stored_and_reaches_worker(client):
    v0 = config(client)["version"]
    rules = client.get("/api/settings/rules").json()
    rules["no_helmet"]["fraction"] = 0.5
    rules["lifecycle"]["cooldown_s"] = 120
    r = client.put("/api/settings/rules", json=rules)
    assert r.status_code == 200 and r.json() == rules
    assert client.get("/api/settings/rules").json() == rules
    cfg = config(client)
    assert cfg["version"] == v0 + 1 and cfg["rules"] == rules


def test_rules_validation(client):
    good = client.get("/api/settings/rules").json()
    for path, value in ((("no_helmet", "fraction"), 1.5), (("no_helmet", "window_s"), 0), (("no_vest", "min_observations"), 0),
                        (("fire", "k"), 6), (("lifecycle", "retrack_iou"), -0.1), (("smoking", "m"), "many")):
        bad = {k: dict(v) for k, v in good.items()}
        bad[path[0]][path[1]] = value
        assert client.put("/api/settings/rules", json=bad).status_code == 422, (path, value)
    assert client.get("/api/settings/rules").json() == good


def test_rules_survive_restart(settings):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    with TestClient(create_app(settings, run_sources=False)) as c:
        rules = c.get("/api/settings/rules").json()
        rules["zone_intrusion"]["min_observations"] = 9
        rules["smoking"]["everywhere"] = True
        c.put("/api/settings/rules", json=rules)
    with TestClient(create_app(settings, run_sources=False)) as c:
        stored = c.get("/api/settings/rules").json()
        assert stored["zone_intrusion"]["min_observations"] == 9 and stored["smoking"]["everywhere"] is True


def test_old_defaults_in_db_are_upgraded_once(settings):
    from fastapi.testclient import TestClient

    from backend.app.db.models import SettingRow
    from backend.app.main import create_app

    def rewrite(c, change):
        with c.app.state.app.session_factory() as db:
            row = db.get(SettingRow, "rules")
            raw = {k: dict(v) for k, v in row.value.items()}
            change(raw)
            row.value = raw
            db.get(SettingRow, "rules_defaults_version").value = 1
            db.commit()

    def old(raw):
        raw["zone_intrusion"] = {"window_s": 1.5, "fraction": 0.7, "min_observations": 1}
        raw["smoking"] = {"k": 2, "m": 5}
        raw["no_helmet"]["fraction"] = 0.5

    with TestClient(create_app(settings, run_sources=False)) as c:
        rewrite(c, old)
    with TestClient(create_app(settings, run_sources=False)) as c:
        rules = c.get("/api/settings/rules").json()
        assert rules["zone_intrusion"]["min_observations"] == 4
        assert rules["smoking"] == {"k": 3, "m": 5, "upper_fraction": 0.33, "everywhere": False}
        assert rules["no_helmet"]["fraction"] == 0.5
        rules["zone_intrusion"]["min_observations"] = 1
        c.put("/api/settings/rules", json=rules)
    with TestClient(create_app(settings, run_sources=False)) as c:
        assert c.get("/api/settings/rules").json()["zone_intrusion"]["min_observations"] == 1

    def custom(raw):
        raw["zone_intrusion"]["min_observations"] = 2
        raw["smoking"] = {"k": 1, "m": 3}

    with TestClient(create_app(settings, run_sources=False)) as c:
        rewrite(c, custom)
    with TestClient(create_app(settings, run_sources=False)) as c:
        rules = c.get("/api/settings/rules").json()
        assert rules["zone_intrusion"]["min_observations"] == 2 and (rules["smoking"]["k"], rules["smoking"]["m"]) == (1, 3)
