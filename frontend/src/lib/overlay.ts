import { EVENT_LABEL, EVENT_SEVERITY, SEVERITY_COLOR, SEVERITY_RANK, ZONE_COLOR, ZONE_TYPE_LABEL } from "../types";
import type { DetectionsMessage, PersonBox, Point, Severity, Zone } from "../types";
import { toCanvas, type Rect } from "./geometry";
import { HANDLE_RADIUS, type PolygonDraft } from "./polygon";

export interface Layers {
  people: boolean;
  ppe: boolean;
  zones: boolean;
  labels: boolean;
}

export const DEFAULT_LAYERS: Layers = { people: true, ppe: false, zones: true, labels: true };

// воркер на стенде шлет рамки раз в 1-2 с, иначе мигают
export const MAX_AGE_MS = 2500;
const OK = "#35a46b";
const NEUTRAL = "#8fb8de";
const INK = "#15191d";
const FONT = "600 12px 'Golos Text', system-ui, sans-serif";

export function isStale(msg: DetectionsMessage, now: number, delayMs = 0): boolean {
  return now - msg.ts > MAX_AGE_MS + delayMs;
}

// видео отстает от рамок на задержку плеера, берем последнее сообщение не новее now - delay и выкидываем старше
export function takeSynced(queue: DetectionsMessage[], now: number, delayMs: number): DetectionsMessage | undefined {
  for (let i = queue.length - 1; i >= 0; i--) {
    if (now - queue[i].ts >= delayMs) {
      const msg = queue[i];
      queue.splice(0, i);
      return msg;
    }
  }
  return undefined;
}

export function worstSeverity(person: PersonBox): Severity | null {
  let worst: Severity | null = null;
  for (const v of person.violations) {
    const s = EVENT_SEVERITY[v];
    if (worst === null || SEVERITY_RANK[s] > SEVERITY_RANK[worst]) worst = s;
  }
  return worst;
}

export function hasThreat(msg: DetectionsMessage): boolean {
  return msg.people.some((p) => p.violations.length > 0) || msg.objects.some((o) => o.cls === "fire" || o.cls === "smoke");
}

function personColor(person: PersonBox): string {
  const worst = worstSeverity(person);
  if (worst) return SEVERITY_COLOR[worst];
  const statuses = Object.values(person.ppe);
  return statuses.length > 0 && statuses.every((s) => s === "ok" || s === "not_required") ? OK : NEUTRAL;
}

function tag(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, color: string, below = false) {
  ctx.font = FONT;
  const w = ctx.measureText(text).width + 10;
  const top = below ? y : y - 20 < 0 ? y : y - 20;
  ctx.fillStyle = color;
  ctx.fillRect(x, top, w, 20);
  ctx.fillStyle = INK;
  ctx.fillText(text, x + 5, top + 14);
}

export function drawDetections(ctx: CanvasRenderingContext2D, msg: DetectionsMessage, rect: Rect, layers: Layers): void {
  if (layers.people) {
    for (const p of msg.people) {
      const b = toCanvas(p.bbox, rect);
      const color = personColor(p);
      ctx.lineWidth = p.violations.length ? 3 : 2;
      ctx.strokeStyle = color;
      ctx.strokeRect(b.x, b.y, b.w, b.h);
      if (layers.labels && p.violations.length) tag(ctx, p.violations.map((v) => EVENT_LABEL[v]).join(", "), b.x, b.y, color);
    }
  }
  for (const o of msg.objects) {
    const hazard = o.cls === "fire" || o.cls === "smoke";
    if (!hazard && !layers.ppe) continue;
    const b = toCanvas(o.bbox, rect);
    const color = hazard ? SEVERITY_COLOR.high : o.cls.startsWith("no_") ? SEVERITY_COLOR.medium : OK;
    ctx.lineWidth = hazard ? 3 : 1;
    ctx.strokeStyle = color;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    if (layers.labels) tag(ctx, hazard ? EVENT_LABEL[o.cls as "fire" | "smoke"] : `${o.cls} ${o.conf.toFixed(2)}`, b.x, b.y + b.h, color, true);
  }
}

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
    ctx.fillStyle = `${color}22`;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.setLineDash(zone.type === "restricted" ? [] : [8, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
    const [x, y] = zone.polygon.reduce((a, p) => (p[1] < a[1] ? p : a));
    tag(ctx, `${zone.name} · ${ZONE_TYPE_LABEL[zone.type].toLowerCase()}`, rect.x + x * rect.w, rect.y + y * rect.h, color, true);
  }
}

export function drawDraft(ctx: CanvasRenderingContext2D, draft: PolygonDraft, rect: Rect, color: string): void {
  if (draft.points.length === 0) return;
  path(ctx, draft.points, rect, draft.closed);
  if (draft.closed) {
    ctx.fillStyle = `${color}33`;
    ctx.fill();
  }
  ctx.lineWidth = 2;
  ctx.strokeStyle = color;
  ctx.stroke();
  draft.points.forEach(([x, y], i) => {
    const first = i === 0 && !draft.closed;
    ctx.beginPath();
    ctx.arc(rect.x + x * rect.w, rect.y + y * rect.h, first ? HANDLE_RADIUS : 6, 0, Math.PI * 2);
    ctx.fillStyle = first ? "#ffffff" : color;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = INK;
    ctx.stroke();
  });
}
