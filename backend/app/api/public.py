from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.analytics import SHIFTS_KEY, Shifts, load_shifts
from backend.app.db.models import EventRow

from backend.app.services.auth import websocket_user
from backend.app.state import AppState, get_current_user, get_db, get_state, get_state_ws, require_admin, set_setting

router = APIRouter(tags=["public"])

LATENCY_SAMPLE = 100


@router.get("/api/health")
async def health(_user=Depends(get_current_user), state: AppState = Depends(get_state)) -> dict:
    sources = state.sources.status()
    worker_ok = state.worker_alive()
    stats = state.worker_stats if worker_ok else {}
    problems = []
    if not sources["mediamtx"]["ok"]:
        problems.append(sources["mediamtx"]["error"])
    if not worker_ok:
        problems.append("воркер не отвечает")
    problems += list(stats.get("warnings") or [])
    for cam_id, cam in sources["cameras"].items():
        if cam["error"]:
            problems.append(f"камера {cam_id}: {cam['error']}")
    if state.max_state["error"]:
        problems.append(f"MAX: {state.max_state['error']}")
    for cam_id, cam in (stats.get("cameras") or {}).items():
        if not cam.get("online"):
            problems.append(f"камера {cam_id}: воркер не получает кадры" + (f" ({cam['error']})" if cam.get("error") else ""))
    return {
        "status": "ok" if not problems else "degraded",
        "problems": problems,
        "backend": {"ok": True},
        "mediamtx": sources["mediamtx"],
        "worker": {"ok": worker_ok, "detector": stats.get("detector"), "device": stats.get("device")},
        "sources": sources["cameras"],
    }


def _summary(values: list[int]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"avg_ms": round(sum(ordered) / len(ordered)), "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            "events": len(ordered)}


@router.get("/api/stats")
async def stats(_user=Depends(get_current_user), db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> dict:
    recent = db.execute(select(EventRow.latency_ms).where(EventRow.latency_ms.is_not(None))
                        .order_by(EventRow.started_at.desc()).limit(LATENCY_SAMPLE)).scalars().all()
    return {"worker_alive": state.worker_alive(), **(state.worker_stats if state.worker_alive() else {}),
            "latency": {"capture_to_event": _summary(list(recent)),
                        "capture_to_max": _summary(list(state.notifier.latency_ms)) if state.notifier else None},
            "subscribers": state.hub.counts()}


@router.get("/api/settings/shifts", response_model=Shifts)
async def get_shifts(_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return {"shifts": load_shifts(db)}


@router.put("/api/settings/shifts", response_model=Shifts)
async def put_shifts(body: Shifts, _user=Depends(require_admin), db: Session = Depends(get_db)):
    set_setting(db, SHIFTS_KEY, [s.model_dump() for s in body.shifts])
    db.commit()
    return body


REFERENCE_MS = 3.0
CAPACITY_REFERENCE = [
    "свободный чип LinQ: инференс 2–3 мс → 13–20 камер с плитками (расчёт)",
    "общий стенд организаторов: 1–2 камеры (замер)",
    "камера = 25 инференсов/с: ppe 6, people 6, heads 12, hazard 1",
]


@router.get("/api/capacity")
async def capacity(cameras: int = Query(default=20, ge=1, le=1000), fps: float = Query(default=6, gt=0, le=30),
                   tiles: bool = True, _user=Depends(get_current_user), state: AppState = Depends(get_state)) -> dict:
    hazard = state.config.worker.hazard_fps
    rates = {"ppe": fps, "people": fps, "heads": fps * 2 if tiles else fps, "hazard": hazard}
    alive = state.worker_alive()
    infer = (state.worker_stats.get("inference") or {}) if alive else {}
    rows, chip_ms = [], 0.0
    for model, rate in rates.items():
        m = infer.get(model)
        # avg_ms в воркере — на весь пакет
        ms = m["avg_ms"] / max(1, m.get("batch") or 1) if m else REFERENCE_MS
        chip_ms += rate * ms
        rows.append({"model": model, "per_s": rate, "ms": round(ms, 2), "measured": m is not None})
    measured = any(r["measured"] for r in rows)
    per_chip = math.floor(1000 / chip_ms) if chip_ms > 0 else 0
    return {
        "cameras": cameras, "fps": fps, "tiles": tiles,
        "infer_per_s": round(sum(rates.values()), 1), "models": rows,
        "chip_s_per_camera_s": round(chip_ms / 1000, 4), "per_accelerator": per_chip,
        "accelerators": math.ceil(cameras / per_chip) if per_chip else None,
        "source": "measured" if measured else "reference",
        "detector": state.worker_stats.get("detector") if alive else None,
        "note": None if alive else f"воркер не отвечает, справочно {REFERENCE_MS:g} мс на инференс",
        "reference": CAPACITY_REFERENCE,
    }


@router.get("/api/client-config")
# браузеру для webrtc и синка рамок
async def client_config(_user=Depends(get_current_user), state: AppState = Depends(get_state)) -> dict:
    return {"video": state.settings.video_mode, "webrtc_port": state.settings.mediamtx_webrtc_port,
            "hls_port": state.settings.mediamtx_hls_port,
            "overlay_delay_ms": state.config.worker.overlay_delay_ms + round(state.settings.sync_lag_s * 1000)}


@router.websocket("/ws/detections")
async def ws_detections(ws: WebSocket, camera_id: int):
    state = get_state_ws(ws)
    if websocket_user(ws, state.session_factory, state.settings.auth_enabled, state.settings.internal_token) is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    state.hub.subscribe_detections(camera_id, ws)
    try:
        while True:
            await ws.receive_text()  # от браузера ничего не ждем, просто держим соединение
    except WebSocketDisconnect:
        pass
    finally:
        state.hub.unsubscribe_detections(camera_id, ws)


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    state = get_state_ws(ws)
    if websocket_user(ws, state.session_factory, state.settings.auth_enabled, state.settings.internal_token) is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    state.hub.subscribe_events(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.hub.unsubscribe_events(ws)
