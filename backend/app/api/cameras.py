from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.models import CameraRow, EventRow
from backend.app.services.sources import Desired
from backend.app.state import AppState, bump_config_version, get_current_user, get_db, get_state, require_admin
from shared.schemas import Camera, CameraCreate, CameraUpdate, EventStatus, SourceType

router = APIRouter(prefix="/api", tags=["cameras"])

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts"}


def sync_sources(db: Session, state: AppState) -> None:
    rows = db.execute(select(CameraRow).where(CameraRow.enabled.is_(True))).scalars().all()
    state.sources.apply([Desired(r.id, r.source_type, r.source) for r in rows])


def to_schema(row: CameraRow, state: AppState, open_events: int = 0) -> Camera:
    return Camera(id=row.id, name=row.name, source_type=row.source_type, source=row.source,
                  required_ppe=row.required_ppe or [], enabled=row.enabled,
                  status="online" if state.camera_online(row.id) else "offline", open_events=open_events)


def _open_counts(db: Session) -> dict[int, int]:
    q = select(EventRow.camera_id, func.count()).where(EventRow.status == EventStatus.open.value).group_by(EventRow.camera_id)
    return dict(db.execute(q).all())


def _validate_source(source_type: SourceType, source: str, state: AppState) -> None:
    if source_type == SourceType.rtsp:
        if not source.lower().startswith(("rtsp://", "rtsps://")):
            raise HTTPException(422, "адрес камеры должен начинаться с rtsp:// или rtsps://")
    else:
        videos = (state.settings.data_dir / "videos").resolve()
        path = (videos / source).resolve()
        if videos not in path.parents or not path.is_file():
            raise HTTPException(422, f"нет видеофайла {source} в data/videos")


def _get(db: Session, camera_id: int) -> CameraRow:
    row = db.get(CameraRow, camera_id)
    if row is None:
        raise HTTPException(404, "камера не найдена")
    return row


@router.get("/cameras", response_model=list[Camera])
async def list_cameras(_user=Depends(get_current_user), db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    counts = _open_counts(db)
    rows = db.execute(select(CameraRow).order_by(CameraRow.id)).scalars().all()
    return [to_schema(r, state, counts.get(r.id, 0)) for r in rows]


@router.post("/cameras", response_model=Camera, status_code=201)
async def create_camera(body: CameraCreate, _user=Depends(require_admin), db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    _validate_source(body.source_type, body.source, state)
    row = CameraRow(name=body.name, source_type=body.source_type.value, source=body.source,
                    required_ppe=[p.value for p in body.required_ppe], enabled=body.enabled)
    db.add(row)
    bump_config_version(db)
    db.commit()
    sync_sources(db, state)
    return to_schema(row, state)


@router.get("/cameras/{camera_id}", response_model=Camera)
async def get_camera(camera_id: int, _user=Depends(get_current_user), db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    return to_schema(_get(db, camera_id), state, _open_counts(db).get(camera_id, 0))


@router.patch("/cameras/{camera_id}", response_model=Camera)
async def update_camera(camera_id: int, body: CameraUpdate, db: Session = Depends(get_db),
                        state: AppState = Depends(get_state), _user=Depends(require_admin)):
    row = _get(db, camera_id)
    data = body.model_dump(exclude_unset=True, mode="json")
    if "source" in data or "source_type" in data:
        _validate_source(SourceType(data.get("source_type", row.source_type)), data.get("source", row.source), state)
    for key, value in data.items():
        setattr(row, key, value)
    bump_config_version(db)
    db.commit()
    sync_sources(db, state)
    return to_schema(row, state, _open_counts(db).get(camera_id, 0))


@router.delete("/cameras/{camera_id}", status_code=204)
async def delete_camera(camera_id: int, _user=Depends(require_admin), db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    db.delete(_get(db, camera_id))
    bump_config_version(db)
    db.commit()
    sync_sources(db, state)


@router.get("/videos")
async def list_videos(_user=Depends(get_current_user), state: AppState = Depends(get_state)) -> list[dict]:
    videos = state.settings.data_dir / "videos"
    if not videos.exists():
        return []
    return [{"name": p.name, "size": p.stat().st_size}
            for p in sorted(videos.iterdir()) if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
