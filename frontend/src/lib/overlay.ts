import { EVENT_LABEL, EVENT_SEVERITY, SEVERITY_COLOR } from "../types";
import type { DetectionsMessage, PersonBox, Severity } from "../types";
import { toCanvas } from "./geometry";
import type { Rect } from "./geometry";

export interface Layers {
  people: boolean;
  ppe: boolean;  // мелкие рамки сиз
  zones: boolean;
  labels: boolean;
}

export const DEFAULT_LAYERS: Layers = { people: true, ppe: false, zones: true, labels: true };

export const MAX_AGE_MS = 1000;
const OK = "#3fb97f";
const NEUTRAL = "#9aa5b1";
const ALWAYS_VISIBLE: Record<string, string> = { fire: SEVERITY_COLOR.high, smoke: SEVERITY_COLOR.high };

// старше секунды не рисуем, лучше пусто чем рамка не там
export function isStale(msg: DetectionsMessage, now: number, delayMs = 0): boolean {
  return now - msg.ts > MAX_AGE_MS + delayMs;
}

export function worstSeverity(person: PersonBox): Severity | null {
  let worst: Severity | null = null;
  const rank = { low: 0, medium: 1, high: 2 };
  for (const v of person.violations) {
    const s = EVENT_SEVERITY[v];
    if (worst === null || rank[s] > rank[worst]) worst = s;
  }
  return worst;
}

export function personColor(person: PersonBox): string {
  const worst = worstSeverity(person);
  if (worst) return SEVERITY_COLOR[worst];
  const statuses = Object.values(person.ppe);
  return statuses.length > 0 && statuses.every((s) => s === "ok" || s === "not_required") ? OK : NEUTRAL;
}

export function personLabel(person: PersonBox): string {
  return person.violations.map((v) => EVENT_LABEL[v]).join(", ");
}

function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, color: string) {
  ctx.font = "13px 'Segoe UI', sans-serif";
  const w = ctx.measureText(text).width + 8;
  const top = y - 18 < 0 ? y : y - 18;
  ctx.fillStyle = color;
  ctx.fillRect(x, top, w, 18);
  ctx.fillStyle = "#0e1116";
  ctx.fillText(text, x + 4, top + 13);
}

export function drawDetections(ctx: CanvasRenderingContext2D, msg: DetectionsMessage, rect: Rect, layers: Layers): void {
  if (layers.people) {
    for (const p of msg.people) {
      const b = toCanvas(p.bbox, rect);
      const color = personColor(p);
      ctx.lineWidth = p.violations.length ? 3 : 2;
      ctx.strokeStyle = color;
      ctx.strokeRect(b.x, b.y, b.w, b.h);
      const text = personLabel(p);
      if (layers.labels && text) label(ctx, text, b.x, b.y, color);
    }
  }
  for (const o of msg.objects) {
    const always = ALWAYS_VISIBLE[o.cls];
    if (!always && !layers.ppe) continue;
    const b = toCanvas(o.bbox, rect);
    const color = always ?? (o.cls.startsWith("no_") ? SEVERITY_COLOR.medium : OK);
    ctx.lineWidth = always ? 3 : 1;
    ctx.strokeStyle = color;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    if (layers.labels) label(ctx, `${o.cls} ${o.conf.toFixed(2)}`, b.x, b.y + b.h + 18, color);
  }
}
