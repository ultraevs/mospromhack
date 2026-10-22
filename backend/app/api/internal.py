from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import CameraRow, EventRow
from backend.app.services import events as svc
from backend.app.services.rules import load_rules
from backend.app.state import AppState, get_config_version, get_db, get_state, get_state_ws, require_internal
from shared.schemas import Event, EventCreate, EventUpdate, WorkerCamera, WorkerConfig, Zone

log = logging.getLogger("backend.internal")

# все для воркера, с X-Internal-Token
router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/config", response_model=WorkerConfig, dependencies=[Depends(require_internal)])
async def worker_config(db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    rows = db.execute(select(CameraRow).where(CameraRow.enabled.is_(True)).order_by(CameraRow.id)).scalars().all()
    cameras = [
        WorkerCamera(id=r.id, name=r.name, rtsp_url=state.sources.rtsp_url(r.id), required_ppe=r.required_ppe or [],
                     zones=[Zone.model_validate(z) for z in r.zones])
        for r in rows
    ]
    return WorkerConfig(version=get_config_version(db), cameras=cameras, rules=load_rules(db, state.config))


@router.post("/events", response_model=Event, status_code=201, dependencies=[Depends(require_internal)])
async def create_event(body: EventCreate, db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    row = svc.create_event(db, body)
    log.info("событие %s: %s, камера %s, задержка %s мс", row.id, row.type, row.camera_id, row.latency_ms)
    await state.hub.send_event(svc.message("event_created", row))
    return svc.to_schema(row)


@router.patch("/events/{event_id}", response_model=Event, dependencies=[Depends(require_internal)])
async def update_event(event_id: str, body: EventUpdate, db: Session = Depends(get_db),
                       state: AppState = Depends(get_state)):
    row = db.get(EventRow, event_id)
    if row is None:
        raise HTTPException(404, "событие не найдено")
    if body.status is not None:
        row.status = body.status.value
    if body.ended_at is not None:
        row.ended_at = svc._naive_utc(body.ended_at)
    db.commit()
    await state.hub.send_event(svc.message("event_updated", row))
    return svc.to_schema(row)


@router.post("/stats", status_code=204, dependencies=[Depends(require_internal)])
async def post_stats(body: dict[str, Any], state: AppState = Depends(get_state)):
    state.worker_stats = body
    state.worker_seen = time.monotonic()


@router.websocket("/detections")
async def detections_in(ws: WebSocket):
    state = get_state_ws(ws)
    if ws.headers.get("x-internal-token") != state.settings.internal_token:
        await ws.close(code=4401)
        return
    await ws.accept()
    log.info("воркер подключился к /internal/detections")
    try:
        while True:
            text = await ws.receive_text()
            try:
                camera_id = int(json.loads(text)["camera_id"])
            except (ValueError, KeyError, TypeError):
                log.warning("сообщение с рамками без camera_id отброшено")
                continue
            await state.hub.send_detections(camera_id, text)
    except WebSocketDisconnect:
        log.warning("воркер отключился от /internal/detections")
