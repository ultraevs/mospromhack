import io
import zipfile

from backend.app.services.sources import SourceManager
from backend.tests.conftest import INTERNAL
from backend.tests.test_auth import admin, app, viewer  # noqa: F401  фикстуры с логином


def up(client, *files):
    return client.post("/api/uploads", files=[("files", f) for f in files])


def test_upload_video(client, settings):
    r = up(client, ("цех 1.MP4", b"fake video", "video/mp4"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_type"] == "file" and body["source"].endswith(".mp4")
    assert (settings.data_dir / "videos" / body["source"]).read_bytes() == b"fake video"
    assert body["source"] in [v["name"] for v in client.get("/api/videos").json()]


def test_upload_images_and_camera(client, settings):
    r = up(client, ("a.jpg", b"1", "image/jpeg"), ("b.PNG", b"2", "image/png"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_type"] == "images"
    assert client.get("/api/images").json() == [{"name": body["source"], "count": 2}]

    r = client.post("/api/cameras", json={"name": "Стоп-кадры", **body})
    assert r.status_code == 201, r.text
    assert r.json()["source_type"] == "images"
    cams = client.get("/internal/config", headers=INTERNAL).json()["cameras"]
    assert [c["stills"] for c in cams if c["id"] == r.json()["id"]] == [True]
    assert client.post("/api/cameras", json={"name": "x", "source_type": "images", "source": "nope"}).status_code == 422
    assert client.post("/api/cameras", json={"name": "x", "source_type": "images", "source": "../videos"}).status_code == 422


def test_upload_zip(client, settings):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("photos/one.jpg", b"1")
        z.writestr("../../evil.jpg", b"2")
        z.writestr("run.exe", b"x")
        z.writestr("deep/dir/", b"")
    r = up(client, ("стройка.zip", buf.getvalue(), "application/zip"))
    assert r.status_code == 200, r.text
    folder = settings.data_dir / "images" / r.json()["source"]
    assert sorted(p.name for p in folder.iterdir()) == ["evil.jpg", "one.jpg"]
    assert not (settings.data_dir / "evil.jpg").exists()


def test_upload_rejects(client, settings):
    assert up(client, ("../../x.mp4", b"1", "video/mp4")).status_code == 422
    assert up(client, (r"..\x.jpg", b"1", "image/jpeg")).status_code == 422
    assert up(client, ("run.exe", b"1", "application/octet-stream")).status_code == 422
    assert up(client, ("a.jpg", b"1", "image/jpeg"), ("b.txt", b"1", "text/plain")).status_code == 422
    assert up(client, ("a.mp4", b"1", "video/mp4"), ("b.mp4", b"1", "video/mp4")).status_code == 422
    assert not (settings.data_dir / "images").exists() or not any((settings.data_dir / "images").iterdir())


def test_viewer_cannot_upload(admin, viewer):  # noqa: F811
    assert up(viewer, ("a.jpg", b"1", "image/jpeg")).status_code == 403
    assert viewer.get("/api/images").status_code == 200


def test_images_ffmpeg_command(tmp_path):
    m = SourceManager(videos_dir=tmp_path / "videos", logs_dir=tmp_path, rtsp_port=8554, api_port=9997, active=False)
    cmd = m.slideshow_command(tmp_path / "list.txt", tmp_path / "slides.mp4")
    assert cmd[cmd.index("-f") + 1] == "concat" and cmd[-1].endswith("slides.mp4")
    assert m.images_dir == tmp_path / "images"
