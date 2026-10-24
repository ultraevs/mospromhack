from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import CameraRow, ZoneRow
from backend.app.services.rules import load_rules, save_rules
from backend.app.state import AppState, bump_config_version, get_current_user, get_db, get_state, require_admin
from shared.schemas import RuleSettings, Zone, ZoneCreate, ZoneUpdate

router = APIRouter(prefix="/api", tags=["zones"])


def _check_polygon(polygon: list[list[float]]) -> None:
    if any(not (0.0 <= v <= 1.0) for point in polygon for v in point):
        raise HTTPException(422, "точки полигона задаются в долях кадра, от 0 до 1")


def _get(db: Session, zone_id: int) -> ZoneRow:
    row = db.get(ZoneRow, zone_id)
    if row is None:
        raise HTTPException(404, "зона не найдена")
    return row


@router.get("/cameras/{camera_id}/zones", response_model=list[Zone])
async def list_zones(camera_id: int, _user=Depends(get_current_user), db: Session = Depends(get_db)):
    if db.get(CameraRow, camera_id) is None:
        raise HTTPException(404, "камера не найдена")
    return db.execute(select(ZoneRow).where(ZoneRow.camera_id == camera_id).order_by(ZoneRow.id)).scalars().all()


@router.post("/cameras/{camera_id}/zones", response_model=Zone, status_code=201)
async def create_zone(camera_id: int, body: ZoneCreate, _user=Depends(require_admin), db: Session = Depends(get_db)):
    if db.get(CameraRow, camera_id) is None:
        raise HTTPException(404, "камера не найдена")
    _check_polygon(body.polygon)
    row = ZoneRow(camera_id=camera_id, name=body.name, type=body.type.value, polygon=body.polygon,
                  required_ppe=[p.value for p in body.required_ppe],
                  schedule=body.schedule.model_dump() if body.schedule else None)
    db.add(row)
    bump_config_version(db)
    db.commit()
    return row


@router.patch("/zones/{zone_id}", response_model=Zone)
async def update_zone(zone_id: int, body: ZoneUpdate, _user=Depends(require_admin), db: Session = Depends(get_db)):
    row = _get(db, zone_id)
    data = body.model_dump(exclude_unset=True, mode="json")
    if data.get("polygon") is not None:
        _check_polygon(data["polygon"])
    for key, value in data.items():
        if value is not None or key == "schedule":  # schedule: null - снять расписание
            setattr(row, key, value)
    bump_config_version(db)
    db.commit()
    return row


@router.delete("/zones/{zone_id}", status_code=204)
async def delete_zone(zone_id: int, _user=Depends(require_admin), db: Session = Depends(get_db)):
    db.delete(_get(db, zone_id))
    bump_config_version(db)
    db.commit()


@router.get("/settings/rules", response_model=RuleSettings)
async def get_rules(_user=Depends(require_admin), db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    return load_rules(db, state.config)


@router.put("/settings/rules", response_model=RuleSettings)
async def put_rules(body: RuleSettings, _user=Depends(require_admin), db: Session = Depends(get_db)):
    save_rules(db, body)
    bump_config_version(db)
    db.commit()
    return body
