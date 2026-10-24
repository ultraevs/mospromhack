from __future__ import annotations

import csv
import html
import io
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.models import EventRow
from backend.app.services import events as svc
from backend.app.state import AppState, get_db, get_current_user, get_state
from shared.schemas import Event, EventReview, EventStatus, EventType, Review, Severity

router = APIRouter(prefix="/api", tags=["events"], dependencies=[Depends(get_current_user)])


class EventPage(BaseModel):
    items: list[Event]
    total: int
    limit: int
    offset: int


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _filters(camera_id: int | None = None, zone_id: int | None = None, type: EventType | None = None,
             severity: Severity | None = None, status: EventStatus | None = None, review: Review | None = None,
             from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None) -> list:
    conds = []
    if camera_id is not None:
        conds.append(EventRow.camera_id == camera_id)
    if zone_id is not None:
        conds.append(EventRow.zone_id == zone_id)
    if type is not None:
        conds.append(EventRow.type == type.value)
    if severity is not None:
        conds.append(EventRow.severity == severity.value)
    if status is not None:
        conds.append(EventRow.status == status.value)
    if review is not None:
        conds.append(EventRow.review == review.value)
    if from_ is not None:
        conds.append(EventRow.started_at >= _naive(from_))
    if to is not None:
        conds.append(EventRow.started_at <= _naive(to))
    return conds


@router.get("/events", response_model=EventPage)
async def list_events(
    conds: list = Depends(_filters),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.execute(select(func.count()).select_from(EventRow).where(*conds)).scalar_one()
    rows = db.execute(select(EventRow).where(*conds).order_by(EventRow.started_at.desc(), EventRow.id.desc())
                      .limit(limit).offset(offset)).scalars().all()
    return EventPage(items=[svc.to_schema(r) for r in rows], total=total, limit=limit, offset=offset)


# подписи как во фронте (types.ts), охране труда коды не нужны
TYPE_LABEL = {
    "no_helmet": "Без каски", "no_mask": "Без маски", "no_vest": "Без жилета", "zone_intrusion": "В запретной зоне",
    "smoking": "Курение", "fire": "Огонь", "smoke": "Дым", "running": "Бег", "vehicle_proximity": "Рядом с техникой",
    "fall": "Падение", "lying_immobile": "Лежит неподвижно",
}
SEVERITY_LABEL = {"high": "Высокая", "medium": "Средняя", "low": "Низкая"}
STATUS_LABEL = {"open": "Идёт", "closed": "Закрыто"}
REVIEW_LABEL = {"none": "Не разобрано", "confirmed": "Подтверждено", "false_positive": "Ложное",
                "forwarded": "Передано мастеру"}
EXPORT_LIMIT = 50_000
EXPORT_HEAD = ["ID", "Начало", "Окончание", "Длительность", "Камера", "Зона", "Нарушение", "Важность", "Состояние",
               "Разбор", "Уверенность", "Стоп-кадр"]


def _local(dt: datetime | None) -> str:
    # в базе наивный utc, в отчёт - время сервера
    return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S") if dt else ""


def _duration(r: EventRow) -> str:
    if r.ended_at is None:
        return ""
    sec = max(0, int((r.ended_at - r.started_at).total_seconds()))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}"


def _export_row(r: EventRow) -> list:
    return [r.id, _local(r.started_at), _local(r.ended_at), _duration(r), r.camera_name, r.zone_name or "",
            TYPE_LABEL.get(r.type, r.type), SEVERITY_LABEL.get(r.severity, r.severity),
            STATUS_LABEL.get(r.status, r.status), REVIEW_LABEL.get(r.review, r.review), round(r.confidence, 2),
            r.snapshot_url or ""]


@router.get("/events/export")
async def export_events(conds: list = Depends(_filters), format: str = Query(default="xlsx", pattern="^(xlsx|csv)$"),
                        db: Session = Depends(get_db)):
    rows = db.execute(select(EventRow).where(*conds).order_by(EventRow.started_at.desc(), EventRow.id.desc())
                      .limit(EXPORT_LIMIT)).scalars().all()
    data = [_export_row(r) for r in rows]
    name = f"events_{date.today().isoformat()}.{format}"
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(EXPORT_HEAD)
        w.writerows(data)
        body, media = buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"  # bom чтоб excel понял кириллицу
    else:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Нарушения"
        ws.append(EXPORT_HEAD)
        for row in data:
            ws.append(row)
        ws.freeze_panes = "A2"
        for col, width in zip("ABCDEFGHIJKL", (16, 20, 20, 12, 24, 20, 20, 11, 11, 18, 12, 40)):
            ws.column_dimensions[col].width = width
        buf = io.BytesIO()
        wb.save(buf)
        body, media = buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


REPORT_CSS = """
body{font:13px/1.4 Arial,sans-serif;color:#111;margin:24px}h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:18px 0 6px}
.meta{color:#555}.tiles{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.tile{border:1px solid #bbb;border-radius:6px;padding:8px 12px;min-width:120px}
.tile b{display:block;font-size:20px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#eee}td img{max-width:110px;max-height:70px}.sign{margin-top:32px}.print{margin-bottom:12px;padding:6px 14px;font-size:14px}
tr{page-break-inside:avoid}@media print{.print{display:none}body{margin:0}}
"""


def _e(v) -> str:
    return html.escape(str(v if v is not None else ""))


@router.get("/events/report", response_class=HTMLResponse)
async def report_events(conds: list = Depends(_filters), camera_id: int | None = None,
                        from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None,
                        db: Session = Depends(get_db)):
    e = _e
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if from_ is None and to is None:
        # смена по умолчанию - последние 12 часов
        from_ = now - timedelta(hours=12)
        conds = [*conds, EventRow.started_at >= from_]
    rows = db.execute(select(EventRow).where(*conds).order_by(EventRow.started_at.asc(), EventRow.id.asc())
                      .limit(EXPORT_LIMIT)).scalars().all()
    real = [r for r in rows if r.review != "false_positive"]
    lat = [r.latency_ms for r in real if r.latency_ms is not None]
    by_type = Counter(r.type for r in real).most_common()
    places = Counter((r.camera_name or f"Камера {r.camera_id}", r.zone_name or "") for r in real).most_common(5)
    cam = next((r.camera_name for r in rows if r.camera_name), f"#{camera_id}") if camera_id is not None else ""
    period = f"{_local(_naive(from_)) or 'начала журнала'} — {_local(_naive(to)) or _local(now)}"

    tiles = [("Нарушений", len(real)), ("Критичных", sum(r.severity == "high" for r in real)),
             ("Не разобрано", sum(r.review == "none" for r in real)), ("Ложных исключено", len(rows) - len(real))]
    if lat:
        tiles.insert(3, ("Среднее до тревоги", f"{sum(lat) / len(lat) / 1000:.1f} с"))
    out = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Отчёт по нарушениям за смену</title>"
           f"<style>{REPORT_CSS}</style></head><body>",
           "<button class='print' onclick='window.print()'>Печать / сохранить в PDF</button>",
           "<h1>Отчёт по нарушениям за смену</h1>",
           f"<div class='meta'>Период: {e(period)}" + (f"<br>Камера: {e(cam)}" if cam else "")
           + f"<br>Сформирован: {e(_local(now))}</div>",
           "<div class='tiles'>" + "".join(f"<div class='tile'><b>{e(v)}</b>{e(k)}</div>" for k, v in tiles) + "</div>"]
    if by_type:
        out.append("<h2>По типам</h2><table><tr><th>Нарушение</th><th>Кол-во</th></tr>"
                   + "".join(f"<tr><td>{e(TYPE_LABEL.get(t, t))}</td><td>{n}</td></tr>" for t, n in by_type) + "</table>")
    if places:
        out.append("<h2>Проблемные места</h2><table><tr><th>Камера</th><th>Зона</th><th>Кол-во</th></tr>"
                   + "".join(f"<tr><td>{e(c)}</td><td>{e(z or '—')}</td><td>{n}</td></tr>" for (c, z), n in places)
                   + "</table>")
    out.append("<h2>Журнал</h2><table><tr><th>№</th><th>Время</th><th>Нарушение</th><th>Камера / зона</th>"
               "<th>Разбор</th><th>Комментарий</th><th>Кадр</th></tr>")
    for i, r in enumerate(rows, 1):
        place = r.camera_name + (f" / {r.zone_name}" if r.zone_name else "")
        note = r.details.get("comment", "") if isinstance(r.details, dict) else ""  # комментарий оператора лежит в details
        img = f"<img src='{e(r.snapshot_url)}' alt=''>" if r.snapshot_url else ""
        out.append(f"<tr><td>{i}</td><td>{e(_local(r.started_at))}</td><td>{e(TYPE_LABEL.get(r.type, r.type))}</td>"
                   f"<td>{e(place)}</td><td>{e(REVIEW_LABEL.get(r.review, r.review))}</td><td>{e(note)}</td>"
                   f"<td>{img}</td></tr>")
    if not rows:
        out.append("<tr><td colspan='7'>Нарушений за период нет</td></tr>")
    out.append("</table><p class='sign'>Подпись ответственного: ______________________ / ______________ /</p></body></html>")
    return HTMLResponse("".join(out))


@router.get("/events/{event_id}", response_model=Event)
async def get_event(event_id: str, db: Session = Depends(get_db)):
    row = db.get(EventRow, event_id)
    if row is None:
        raise HTTPException(404, "событие не найдено")
    return svc.to_schema(row)


@router.patch("/events/{event_id}", response_model=Event)
async def review_event(event_id: str, body: EventReview, db: Session = Depends(get_db),
                       state: AppState = Depends(get_state)):
    row = db.get(EventRow, event_id)
    if row is None:
        raise HTTPException(404, "событие не найдено")
    row.review = body.review.value
    db.commit()
    await state.hub.send_event(svc.message("event_updated", row))
    return svc.to_schema(row)
