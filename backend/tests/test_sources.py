from pathlib import Path

from backend.app.services.sources import Desired, SourceManager


def test_publish_command_seeks_to_offset():
    m = SourceManager(Path("v"), Path("l"), 8554, 9997, active=False)
    cmd = m.ffmpeg_command(Desired(1, "file", "a.mp4"), Path("v/a.mp4"), 12.3456)
    i = cmd.index("-i")
    assert cmd[i - 2:i] == ["-ss", "12.346"] and cmd.index("-stream_loop") < i and "libx264" in cmd
    assert "-ss" not in m.ffmpeg_command(Desired(1, "file", "a.mp4"), Path("v/a.mp4"))


def test_publish_caps_default_off():
    cmd = SourceManager(Path("v"), Path("l"), 8554, 9997, active=False).ffmpeg_command(
        Desired(1, "file", "a.mp4"), Path("v/a.mp4"))
    assert "-maxrate" not in cmd and "-vf" not in cmd


def test_publish_caps_applied():
    m = SourceManager(Path("v"), Path("l"), 8554, 9997, active=False, width=640, maxrate="700k")
    cmd = m.ffmpeg_command(Desired(1, "file", "a.mp4"), Path("v/a.mp4"), 1.0)
    assert cmd[cmd.index("-maxrate") + 1] == "700k" and cmd[cmd.index("-bufsize") + 1] == "1400k"
    assert cmd[cmd.index("-vf") + 1] == "scale=640:-2" and cmd.index("-vf") > cmd.index("-i")
