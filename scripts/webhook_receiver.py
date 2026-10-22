#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secret", required=True)
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0, если вебхук шлёт сервис из Docker")
    args = ap.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            expected = "sha256=" + hmac.new(args.secret.encode(), body, hashlib.sha256).hexdigest()
            got = self.headers.get("X-Signature", "")
            valid = hmac.compare_digest(expected, got)
            try:
                event = json.loads(body)
            except ValueError:
                event = {}
            print(f"{datetime.now():%H:%M:%S} подпись {'ВЕРНА' if valid else 'НЕ ВЕРНА'} | "
                  f"{self.headers.get('X-Event-Action')} | {self.headers.get('X-Event-Type')} | {event.get('id')} | "
                  f"камера {event.get('camera_name')} | стоп-кадр {event.get('snapshot_url')} | клип {event.get('clip_url')}",
                  flush=True)
            self.send_response(200 if valid else 401)
            self.end_headers()

        def log_message(self, *a):
            pass

    print(f"слушаю http://{args.host}:{args.port}/hook", flush=True)
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
