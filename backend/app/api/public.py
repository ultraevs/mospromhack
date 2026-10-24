from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import EventRow

from backend.app.services.auth import websocket_user
from backend.app.state import AppState, get_current_user, get_db, get_state, get_state_ws

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
    if state.telegram_state["error"]:
        problems.append(f"телеграм: {state.telegram_state['error']}")
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
                        "capture_to_telegram": _summary(list(state.notifier.latency_ms)) if state.notifier else None},
            "subscribers": state.hub.counts()}


@router.get("/api/client-config")
# браузеру для webrtc и синка рамок
async def client_config(_user=Depends(get_current_user), state: AppState = Depends(get_state)) -> dict:
    return {"video": state.settings.video_mode, "webrtc_port": state.settings.mediamtx_webrtc_port,
            "hls_port": state.settings.mediamtx_hls_port,
            "overlay_delay_ms": state.config.worker.overlay_delay_ms + round(state.settings.sync_lag_s * 1000)}


@router.websocket("/ws/detections")
async def ws_detections(ws: WebSocket, camera_id: int):
    state = get_state_ws(ws)
    if websocket_user(ws, state.session_factory, state.settings.auth_enabled) is None:
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
    user = websocket_user(ws, state.session_factory, state.settings.auth_enabled)
    if user is None:
        await ws.close(code=4401)
        return
    if user.role != "admin":
        await ws.close(code=4403)
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
