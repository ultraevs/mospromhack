#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linq_remote import BASE, ENV, REPO, put, shell, stream  # noqa: E402
from linq_stand import Stand  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = f"~/work/{BASE}/app/data/videos"
DEPS = ["httpx", "websockets", "pika", "aiohttp", "protobuf", "psutil"]
SERVICES = ["bridge", "worker"]
PORT = 18431


def _bridge_prefix() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("BRIDGE_PREFIX=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    return ""


COMMANDS = {
    "bridge": f"BRIDGE_PREFIX={_bridge_prefix()} python scripts/linq_bridge.py stand --port {PORT}",
    "worker": f"BACKEND_HOST=127.0.0.1 BACKEND_PORT={PORT} SYNC_VIDEOS_DIR={VIDEOS} python -m worker.app.main",
}


def stand_config() -> bytes:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg["worker"]["detector"] = "linq"
    cfg["worker"]["decode"]["hwaccel"] = "none"
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).encode()


def push(stand: Stand) -> None:
    files = [f for d in ("worker/app", "shared") for f in (ROOT / d).rglob("*.py")]
    files += [ROOT / "worker/__init__.py", ROOT / "scripts/linq_bridge.py"]
    for f in files:
        put(stand, f, f"{REPO}/{f.relative_to(ROOT).as_posix()}")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yaml"
        cfg.write_bytes(stand_config())
        stand.upload(cfg, f"{REPO}/config.yaml")
    wheel = next((ROOT / "hack_materials" / "stand").glob("tpu_pycloud_format-*.whl"))
    put(stand, wheel, f"{BASE}/sdk/{wheel.name}")
    shell(stand, f"pip install -q ~/work/{BASE}/sdk/{wheel.name} {' '.join(DEPS)} 2>&1 | grep -v -i warn; "
                 "ls models/*/linq/meta.yaml; ls /auto/cache/$(whoami)/*.tpu")


def start(stand: Stand, only: list[str]) -> None:
    parts = []
    for name in only or SERVICES:
        parts.append(f"if [ -f run/{name}.pid ] && kill -0 $(cat run/{name}.pid) 2>/dev/null; then echo '{name} уже работает'; "
                     f"else nohup setsid bash -c '{COMMANDS[name]}' > logs/{name}.log 2>&1 < /dev/null & echo $! > run/{name}.pid; "
                     f"echo '{name} запущен'; sleep 3; fi")
    shell(stand, "mkdir -p logs run && " + "; ".join(parts))


def stop(stand: Stand, only: list[str]) -> None:
    names = " ".join(reversed(only or SERVICES))
    shell(stand, f"for p in {names}; do [ -f run/$p.pid ] && kill -- -$(cat run/$p.pid) 2>/dev/null && echo \"остановлен $p\"; "
                 "rm -f run/$p.pid; done; true")


def status(stand: Stand) -> None:
    shell(stand, f"for p in {' '.join(SERVICES)}; do if [ -f run/$p.pid ] && kill -0 $(cat run/$p.pid) 2>/dev/null; "
                 "then echo \"$p: работает\"; else echo \"$p: не запущен\"; fi; done; tail -n 3 logs/*.log 2>/dev/null")


def main() -> None:
    ap = argparse.ArgumentParser(description="воркер с tpu + мост на стенде; backend/ui у себя, см. docs/LINQ.md")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("push", help="код воркера, мост, config.yaml (detector: linq), зависимости")
    for name in ("start", "stop"):
        p = sub.add_parser(name)
        p.add_argument("services", nargs="*", help=" ".join(SERVICES))
    sub.add_parser("status")
    p = sub.add_parser("logs")
    p.add_argument("service", nargs="?", default="")
    p.add_argument("-n", type=int, default=40)
    args = ap.parse_args()
    bad = set(getattr(args, "services", None) or []) - set(SERVICES)
    if bad:
        ap.error(f"неизвестные сервисы {sorted(bad)}; есть {SERVICES}")

    stand = Stand()
    if args.cmd == "push":
        push(stand)
    elif args.cmd == "start":
        start(stand, args.services)
    elif args.cmd == "stop":
        stop(stand, args.services)
    elif args.cmd == "status":
        status(stand)
    elif args.cmd == "logs":
        stream(stand, f"cd ~/work/{REPO}/logs && tail -n {args.n} {args.service + '.log' if args.service else '*.log'}")


if __name__ == "__main__":
    main()
