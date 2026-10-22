from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from backend.app.state import AppState, get_state, get_state_ws

router = APIRouter(tags=["public"])


@router.get("/api/health")
async def health(state: AppState = Depends(get_state)) -> dict:
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


@router.get("/api/stats")
async def stats(state: AppState = Depends(get_state)) -> dict:
    return {"worker_alive": state.worker_alive(), **(state.worker_stats if state.worker_alive() else {}),
            "subscribers": state.hub.counts()}


@router.get("/api/client-config")
# браузеру для webrtc и синка рамок
async def client_config(state: AppState = Depends(get_state)) -> dict:
    return {"webrtc_port": state.settings.mediamtx_webrtc_port,
            "overlay_delay_ms": state.config.worker.overlay_delay_ms}


@router.websocket("/ws/detections")
async def ws_detections(ws: WebSocket, camera_id: int):
    state = get_state_ws(ws)
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
    await ws.accept()
    state.hub.subscribe_events(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.hub.unsubscribe_events(ws)
