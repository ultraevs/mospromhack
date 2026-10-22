import type { Point, Zone, ZoneType } from "../types";
import type { Rect } from "./geometry";
import { HANDLE_RADIUS } from "./polygon";
import type { PolygonDraft } from "./polygon";

export const ZONE_TYPE_LABEL: Record<ZoneType, string> = {
  restricted: "вход запрещён",
  ppe_required: "обязательны СИЗ",
  flammable: "горючие материалы",
};

export const ZONE_COLOR: Record<ZoneType, string> = {
  restricted: "#f0524f",
  ppe_required: "#4c9aff",
  flammable: "#f59e3b",
};

function path(ctx: CanvasRenderingContext2D, points: Point[], rect: Rect, close: boolean) {
  ctx.beginPath();
  points.forEach(([x, y], i) => {
    const px = rect.x + x * rect.w;
    const py = rect.y + y * rect.h;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  if (close) ctx.closePath();
}

export function drawZones(ctx: CanvasRenderingContext2D, zones: Zone[], rect: Rect, skipId?: number): void {
  for (const zone of zones) {
    if (zone.id === skipId || zone.polygon.length < 3) continue;
    const color = ZONE_COLOR[zone.type];
    path(ctx, zone.polygon, rect, true);
    ctx.fillStyle = color + "26";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.setLineDash(zone.type === "restricted" ? [] : [8, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
    const [x, y] = zone.polygon.reduce((a, p) => (p[1] < a[1] ? p : a));
    const text = `${zone.name} · ${ZONE_TYPE_LABEL[zone.type]}`;
    ctx.font = "12px 'Segoe UI', sans-serif";
    const w = ctx.measureText(text).width + 8;
    const px = rect.x + x * rect.w;
    const py = rect.y + y * rect.h;
    ctx.fillStyle = color;
    ctx.fillRect(px, py, w, 17);
    ctx.fillStyle = "#0e1116";
    ctx.fillText(text, px + 4, py + 12);
  }
}

export function drawDraft(ctx: CanvasRenderingContext2D, draft: PolygonDraft, rect: Rect, color: string): void {
  if (draft.points.length === 0) return;
  path(ctx, draft.points, rect, draft.closed);
  if (draft.closed) {
    ctx.fillStyle = color + "33";
    ctx.fill();
  }
  ctx.lineWidth = 2;
  ctx.strokeStyle = color;
  ctx.stroke();
  draft.points.forEach(([x, y], i) => {
    ctx.beginPath();
    ctx.arc(rect.x + x * rect.w, rect.y + y * rect.h, i === 0 && !draft.closed ? HANDLE_RADIUS : 6, 0, Math.PI * 2);
    ctx.fillStyle = i === 0 && !draft.closed ? "#ffffff" : color;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#0e1116";
    ctx.stroke();
  });
}
