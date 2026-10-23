#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import requests
import websocket

HOST = os.environ.get("LINQ_HOST", "hackathon.lin-q.ru")
BASE = f"http://{HOST}"
CHUNK = 4 * 2**20


def read_creds(path: Path = Path("/creds")) -> tuple[str, str]:
    values = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)
    try:
        return values["jupyter_notebook_log"].strip(), values["jupyter_notebook_pass"].strip()
    except KeyError:
        raise SystemExit("в файле с логинами нет jupyter_notebook_log / jupyter_notebook_pass") from None


class Stand:
    def __init__(self) -> None:
        self.user, password = read_creds()
        self.http = requests.Session()
        page = self.http.get(f"{BASE}/hub/login", timeout=20)
        xsrf = self.http.cookies.get("_xsrf") or (re.search(r'name="_xsrf" value="([^"]+)"', page.text) or [None, ""])[1]
        r = self.http.post(f"{BASE}/hub/login?next=", data={"username": self.user, "password": password, "_xsrf": xsrf},
                           allow_redirects=False, timeout=20)
        if r.status_code not in (302, 303) or "/hub/login" in r.headers.get("location", ""):
            raise SystemExit(f"вход в Jupyter не удался (HTTP {r.status_code})")
        # хаб не выдаёт API-токены (403), поэтому cookie-сессия
        self.api = f"{BASE}/user/{self.user}/api"
        for _ in range(60):
            r = self.http.get(f"{BASE}/hub/spawn/{self.user}", timeout=60)
            r = self.http.get(f"{BASE}/user/{self.user}/tree", timeout=60)
            if r.ok and self.http.get(f"{self.api}/status", timeout=20).ok:
                break
            time.sleep(3)
        else:
            raise SystemExit(f"сервер Jupyter не ответил (последний HTTP {r.status_code})")
        # _xsrf две: у хаба (/hub/) и у сервера пользователя (/user/<логин>/) — нужна вторая
        xsrf = sorted((c for c in self.http.cookies if c.name == "_xsrf"), key=lambda c: c.path.startswith("/user/"))
        self.http.headers["X-XSRFToken"] = xsrf[-1].value if xsrf else ""

    def _check(self, r: requests.Response, what: str) -> requests.Response:
        if not r.ok:
            raise SystemExit(f"{what}: HTTP {r.status_code} {r.text[:300].replace(self.user, '<логин>')}")
        return r

    def mkdirs(self, remote_dir: str) -> None:
        made = self.__dict__.setdefault("_made", set())
        path = ""
        for part in [p for p in remote_dir.split("/") if p]:
            path = f"{path}/{part}" if path else part
            if path in made:
                continue
            made.add(path)
            if self.http.get(f"{self.api}/contents/{path}", params={"content": 0}, timeout=30).status_code == 404:
                self._check(self.http.put(f"{self.api}/contents/{path}", json={"type": "directory"}, timeout=30),
                            f"создание папки {path}")

    def upload(self, local: Path, remote: str) -> None:
        data = local.read_bytes()
        if "/" in remote:
            self.mkdirs(remote.rsplit("/", 1)[0])
        parts = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
        for n, part in enumerate(parts, 1):
            body = {"type": "file", "format": "base64", "name": Path(remote).name, "path": remote,
                    "content": base64.b64encode(part).decode()}
            if len(parts) > 1:
                body["chunk"] = -1 if n == len(parts) else n
            self._check(self.http.put(f"{self.api}/contents/{remote}", json=body, timeout=300), f"загрузка {remote}")
        print(f"загружено {local} -> {remote} ({len(data) / 2**20:.1f} МБ)")

    def download(self, remote: str, local: Path) -> None:
        r = self._check(self.http.get(f"{self.api}/contents/{remote}",
                                      params={"content": 1, "format": "base64", "type": "file"}, timeout=300),
                        f"скачивание {remote}")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(base64.b64decode(r.json()["content"]))
        print(f"скачано {remote} -> {local}")

    def run(self, code: str, timeout: float = 3600) -> bool:
        kernel = self._check(self.http.post(f"{self.api}/kernels", json={"name": "python3"}, timeout=60),
                             "запуск ядра").json()["id"]
        cookie = "; ".join(f"{c.name}={c.value}" for c in self.http.cookies if not c.path.startswith("/hub"))
        ws = websocket.create_connection(f"ws://{HOST}/user/{self.user}/api/kernels/{kernel}/channels",
                                         timeout=timeout, cookie=cookie,
                                         header=[f"X-XSRFToken: {self.http.headers['X-XSRFToken']}"],
                                         origin=BASE)
        msg_id = uuid.uuid4().hex
        ws.send(json.dumps({
            "header": {"msg_id": msg_id, "username": self.user, "session": uuid.uuid4().hex, "msg_type": "execute_request",
                       "version": "5.3"},
            "parent_header": {}, "metadata": {}, "channel": "shell",
            "content": {"code": code, "silent": False, "store_history": False, "user_expressions": {},
                        "allow_stdin": False, "stop_on_error": True},
        }))
        ok = True
        try:
            while True:
                msg = json.loads(ws.recv())
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                kind, content = msg["msg_type"], msg["content"]
                if kind == "stream":
                    (sys.stderr if content["name"] == "stderr" else sys.stdout).write(content["text"])
                elif kind in ("execute_result", "display_data"):
                    print(content["data"].get("text/plain", ""))
                elif kind == "error":
                    ok = False
                    print(f"{content['ename']}: {content['evalue']}", file=sys.stderr)
                    print(re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(content["traceback"][-6:])), file=sys.stderr)
                elif kind == "status" and content["execution_state"] == "idle":
                    break
        finally:
            ws.close()
            self.http.delete(f"{self.api}/kernels/{kernel}", timeout=30)
        return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="выполнить python-код в ядре")
    p.add_argument("code")
    p = sub.add_parser("sh", help="выполнить shell-команду в ядре (через subprocess)")
    p.add_argument("command")
    p = sub.add_parser("upload")
    p.add_argument("local", type=Path)
    p.add_argument("remote")
    p = sub.add_parser("put", help="файлы/папки в папку на стенде, одной сессией")
    p.add_argument("remote_dir")
    p.add_argument("locals", nargs="+", type=Path)
    p = sub.add_parser("download")
    p.add_argument("remote")
    p.add_argument("local", type=Path)
    args = ap.parse_args()

    stand = Stand()
    if args.cmd == "run":
        sys.exit(0 if stand.run(args.code) else 1)
    if args.cmd == "sh":
        code = ("import subprocess, sys\n"
                f"r = subprocess.run({args.command!r}, shell=True, capture_output=True, text=True)\n"
                "sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); print(f'[exit {r.returncode}]')")
        sys.exit(0 if stand.run(code) else 1)
    if args.cmd == "upload":
        stand.upload(args.local, args.remote)
    if args.cmd == "put":
        for local in args.locals:
            files = sorted(f for f in local.rglob("*") if f.is_file()) if local.is_dir() else [local]
            for f in files:
                rel = f.relative_to(local.parent).as_posix()
                stand.upload(f, f"{args.remote_dir.rstrip('/')}/{rel}")
    if args.cmd == "download":
        stand.download(args.remote, args.local)


if __name__ == "__main__":
    main()
