#!/usr/bin/env python3
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_settings  # noqa: E402

PREFIX = "bench-"
PASS_SHARE = 0.9


# N копий одного видео как N камер, смотрим сколько вывозит
def main() -> int:
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", default="factory_floor.mp4", help="файл из data/videos")
    ap.add_argument("--streams", type=int, nargs="+", default=[1, 2, 4, 8], help="сколько камер проверять, по возрастанию")
    ap.add_argument("--seconds", type=float, default=30, help="длительность замера на каждом уровне")
    ap.add_argument("--warmup", type=float, default=10, help="секунд на прогрев после выхода камер на связь")
    ap.add_argument("--separate-encoders", action="store_true", help="свой ffmpeg на каждую камеру")
    ap.add_argument("--url", default=f"http://127.0.0.1:{settings.backend_port}")
    args = ap.parse_args()

    http = httpx.Client(base_url=args.url, timeout=10, headers={"X-Internal-Token": settings.internal_token})
    try:
        health = http.get("/api/health").json()
    except httpx.HTTPError as e:
        print(f"backend не отвечает на {args.url}: {e}. Запусти scripts/dev.ps1")
        return 1
    if not health["worker"]["ok"] or not health["mediamtx"]["ok"]:
        print(f"сервис не готов: {health['problems']}")
        return 1
    others = [c for c in http.get("/api/cameras").json() if c["enabled"] and not c["name"].startswith(PREFIX)]
    if others:
        print(f"внимание: включены ещё {len(others)} камер(ы), они участвуют в нагрузке: {[c['name'] for c in others]}")

    mine: list[int] = []
    rows = []
    try:
        for n in sorted(args.streams):
            while len(mine) < n:
                body = {"name": f"{PREFIX}{len(mine) + 1}", "required_ppe": ["helmet"]}
                if mine and not args.separate_encoders:
                    body |= {"source_type": "rtsp", "source": f"rtsp://127.0.0.1:{settings.mediamtx_rtsp_port}/cam-{mine[0]}"}
                else:
                    body |= {"source_type": "file", "source": args.video}
                r = http.post("/api/cameras", json=body)
                r.raise_for_status()
                mine.append(r.json()["id"])
            ids = {str(i) for i in mine}

            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                cams = http.get("/api/stats").json().get("cameras", {})
                if all(cams.get(i, {}).get("online") for i in ids):
                    break
                time.sleep(1)
            else:
                print(f"{n} потоков: не все камеры вышли на связь за 60 с, замер прерван")
                break
            time.sleep(args.warmup)

            samples = []
            end = time.monotonic() + args.seconds
            while time.monotonic() < end:
                samples.append(http.get("/api/stats").json())
                time.sleep(1)

            proc = [statistics.mean(s["cameras"][i]["proc_fps"] for s in samples if i in s.get("cameras", {})) for i in ids]
            read = [statistics.mean(s["cameras"][i]["read_fps"] for s in samples if i in s.get("cameras", {})) for i in ids]
            target = samples[-1]["target_fps"]["ppe"]
            goal = target
            ppe = [s["inference"]["ppe"] for s in samples if "ppe" in s.get("inference", {})]
            hazard = [s["inference"]["hazard"] for s in samples if "hazard" in s.get("inference", {})]
            ok = min(proc) >= goal * PASS_SHARE
            rows.append({
                "n": n, "ok": ok, "proc_min": min(proc), "proc_avg": statistics.mean(proc), "read": statistics.mean(read),
                "ppe_avg": statistics.mean(x["avg_ms"] for x in ppe) if ppe else 0,
                "ppe_p95": max(x["p95_ms"] for x in ppe) if ppe else 0,
                "hazard_avg": statistics.mean(x["avg_ms"] for x in hazard) if hazard else 0,
                "hazard_fps": statistics.mean(s.get("actual_fps", {}).get("hazard", 0) for s in samples),
                "crops": statistics.mean(s.get("crops_per_s", 0) for s in samples),
                "load": statistics.mean(s.get("loop_load", 0) for s in samples),
                "cpu_worker": statistics.mean(s.get("cpu_cores", {}).get("worker", 0) for s in samples),
                "cpu_decoders": statistics.mean(s.get("cpu_cores", {}).get("decoders", 0) for s in samples),
                "decode": samples[-1].get("decode", {}),
                "device": samples[-1].get("device"), "goal": goal,
            })
            print(f"{n} потоков: обработка мин. {min(proc):.1f} / средн. {statistics.mean(proc):.1f} кадров/с "
                  f"при цели {goal:.1f} -> {'держит' if ok else 'НЕ держит'}")
    finally:
        for cam_id in mine:
            try:
                http.delete(f"/api/cameras/{cam_id}")
            except httpx.HTTPError as e:
                print(f"не удалось удалить камеру {cam_id}: {e}")

    if not rows:
        return 1
    print(f"\nвидео {args.video}, устройство {rows[-1]['device']}, замер {args.seconds:.0f} с на уровень, "
          f"{'свой ffmpeg на каждую камеру' if args.separate_encoders else 'один ffmpeg на все камеры'}\n")
    print("| потоков | обработка мин., к/с | обработка средн., к/с | чтение, к/с | ppe средн., мс | ppe p95, мс | "
          "hazard средн., мс | hazard, тиков/с | вырезок/с | загрузка цикла | ядер: воркер | ядер: декодеры | держит |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['n']} | {r['proc_min']:.1f} | {r['proc_avg']:.1f} | {r['read']:.1f} | {r['ppe_avg']:.0f} | "
              f"{r['ppe_p95']:.0f} | {r['hazard_avg']:.0f} | {r['hazard_fps']:.1f} | {r['crops']:.1f} | {r['load']:.2f} | "
              f"{r['cpu_worker']:.1f} | {r['cpu_decoders']:.1f} | "
              f"{'да' if r['ok'] else 'нет'} |")
    print()
    print(f"чтение камер: {rows[-1]['decode']}; цель {rows[-1]['goal']:g} кадров/с на камеру "
          f"(меняется переменной PPE_FPS при запуске сервиса, например PPE_FPS=4)")
    held = [r for r in rows if r["ok"]]
    if not held:
        print("\nИтог: сервис не держит ни один из проверенных уровней")
    else:
        best = max(held, key=lambda r: r["n"])
        note = " (предел не достигнут, добавь уровни в --streams)" if best["n"] == rows[-1]["n"] else ""
        print(f"\nИтог: сервис держит {best['n']} потоков при {best['goal']:.1f} кадров/с на камеру{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
