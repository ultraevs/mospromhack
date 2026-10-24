from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import CameraRow, EventRow
from backend.app.services import events as svc
from backend.app.services import media
from backend.app.services.bus import BusMessage
from backend.app.services.rules import load_rules
from backend.app.state import (AppState, get_config_version, get_db, get_setting, get_state, get_state_ws,
                               media_root, require_internal, set_setting)
from shared.schemas import Event, EventCreate, EventStatus, EventUpdate, WorkerCamera, WorkerConfig, Zone

log = logging.getLogger("backend.internal")

# все для воркера, с X-Internal-Token
router = APIRouter(prefix="/internal", tags=["internal"])

WORKER_SESSION_KEY = "worker_session"


@router.get("/config", response_model=WorkerConfig, dependencies=[Depends(require_internal)])
async def worker_config(db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    rows = db.execute(select(CameraRow).where(CameraRow.enabled.is_(True)).order_by(CameraRow.id)).scalars().all()
    cameras = [
        WorkerCamera(id=r.id, name=r.name, rtsp_url=state.sources.rtsp_url(r.id), required_ppe=r.required_ppe or [],
                     zones=[Zone.model_validate(z) for z in r.zones], stills=r.source_type == "images")
        for r in rows
    ]
    return WorkerConfig(version=get_config_version(db), cameras=cameras, rules=load_rules(db, state.config))


@router.post("/events", response_model=Event, status_code=201, dependencies=[Depends(require_internal)])
async def create_event(request: Request, db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    files: dict[str, bytes] = {}
    try:
        if request.headers.get("content-type", "").startswith("multipart/"):
            form = await request.form()
            body = EventCreate.model_validate_json(str(form.get("event") or ""))
            for field in ("snapshot", "clean"):
                upload = form.get(field)
                if upload is not None and hasattr(upload, "read"):
                    data = await upload.read()
                    if len(data) > media.MAX_IMAGE_BYTES:
                        raise HTTPException(413, f"{field}: файл больше {media.MAX_IMAGE_BYTES // 2 ** 20} МБ")
                    files[field] = data
        else:
            body = EventCreate.model_validate_json(await request.body())
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False, include_input=False)) from None

    row = svc.create_event(db, body)
    root = media_root(state)
    if "snapshot" in files:
        row.snapshot_url = media.save(root, row.id, media.SNAPSHOT, files["snapshot"])
    if "clean" in files:
        row.snapshot_clean_url = media.save(root, row.id, media.CLEAN, files["clean"])
    if files:
        db.commit()
        prune_media(db, root)
    log.info("событие %s: %s, камера %s, задержка %s мс, стоп-кадров %s", row.id, row.type, row.camera_id,
             row.latency_ms, len(files))
    await state.hub.send_event(svc.message("event_created", row))
    state.bus.publish(BusMessage("opened", svc.to_schema(row), body.captured_ts))
    return svc.to_schema(row)


def prune_media(db: Session, root) -> None:
    has_media = (EventRow.snapshot_url.is_not(None) | EventRow.snapshot_clean_url.is_not(None)
                 | EventRow.clip_url.is_not(None))
    old = db.execute(select(EventRow).where(has_media).order_by(EventRow.started_at.desc())
                     .offset(media.KEEP_EVENTS)).scalars().all()
    for row in old:
        media.drop(root, row.id)
        row.snapshot_url = row.snapshot_clean_url = row.clip_url = None
    if old:
        db.commit()


@router.post("/events/{event_id}/clip", response_model=Event, dependencies=[Depends(require_internal)])
async def upload_clip(event_id: str, clip: UploadFile, db: Session = Depends(get_db),
                      state: AppState = Depends(get_state)):
    row = db.get(EventRow, event_id)
    if row is None:
        raise HTTPException(404, "событие не найдено")
    data = await clip.read()
    if not data:
        raise HTTPException(422, "пустой файл клипа")
    if len(data) > media.MAX_CLIP_BYTES:
        raise HTTPException(413, f"клип больше {media.MAX_CLIP_BYTES // 2 ** 20} МБ")
    row.clip_url = media.save(media_root(state), row.id, media.CLIP, data)
    db.commit()
    log.info("событие %s: клип %.1f МБ", row.id, len(data) / 2 ** 20)
    prune_media(db, media_root(state))
    await state.hub.send_event(svc.message("event_updated", row))
    state.bus.publish(BusMessage("clip_added", svc.to_schema(row)))
    return svc.to_schema(row)


@router.patch("/events/{event_id}", response_model=Event, dependencies=[Depends(require_internal)])
async def update_event(event_id: str, body: EventUpdate, db: Session = Depends(get_db),
                       state: AppState = Depends(get_state)):
    row = db.get(EventRow, event_id)
    if row is None:
        raise HTTPException(404, "событие не найдено")
    was_open = row.status == EventStatus.open.value
    if body.status is not None:
        row.status = body.status.value
    if body.ended_at is not None:
        row.ended_at = svc._naive_utc(body.ended_at)
    db.commit()
    await state.hub.send_event(svc.message("event_updated", row))
    if body.status == EventStatus.closed and was_open:
        state.bus.publish(BusMessage("closed", svc.to_schema(row)))
    return svc.to_schema(row)


@router.post("/stats", status_code=204, dependencies=[Depends(require_internal)])
async def post_stats(body: dict[str, Any], db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    session = body.get("session")
    if session and session != state.worker_session:
        if session != get_setting(db, WORKER_SESSION_KEY):
            for row in svc.close_orphans(db):
                log.warning("событие %s закрыто: воркер перезапущен", row.id)
                await state.hub.send_event(svc.message("event_updated", row))
            set_setting(db, WORKER_SESSION_KEY, session)
            db.commit()
        state.worker_session = session
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
