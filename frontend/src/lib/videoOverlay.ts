import { EVENT_LABEL, EVENT_SEVERITY, SEVERITY_COLOR, type DetectionsMessage, type Zone } from "../types";

type Rect = { x: number; y: number; w: number; h: number };

export function fitVideoRect(width: number, height: number, frameWidth: number, frameHeight: number): Rect {
  if (!width || !height || !frameWidth || !frameHeight) return { x: 0, y: 0, w: width, h: height };
  const scale = Math.min(width / frameWidth, height / frameHeight);
  const w = frameWidth * scale;
  const h = frameHeight * scale;
  return { x: (width - w) / 2, y: (height - h) / 2, w, h };
}

function box([x, y, w, h]: [number, number, number, number], rect: Rect): Rect {
  return { x: rect.x + x * rect.w, y: rect.y + y * rect.h, w: w * rect.w, h: h * rect.h };
}

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, color: string) {
  ctx.font = "600 12px Manrope, sans-serif";
  const width = ctx.measureText(text).width + 14;
  const top = Math.max(4, y - 24);
  ctx.fillStyle = "rgba(8, 12, 11, .9)";
  roundedRect(ctx, x, top, width, 20, 4);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.fillText(text, x + 7, top + 14);
}

export function hasThreat(message: DetectionsMessage): boolean {
  return message.people.some((person) => person.violations.length > 0)
    || message.objects.some((object) => object.cls === "fire" || object.cls === "smoke");
}

export function drawVideoOverlay(ctx: CanvasRenderingContext2D, message: DetectionsMessage | null, zones: Zone[], rect: Rect) {
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  if (zones.length) {
    for (const zone of zones) {
      if (zone.polygon.length < 3) continue;
      const color = zone.type === "restricted" || zone.type === "flammable" ? "#f28f86" : "#b8c8b8";
      ctx.beginPath();
      zone.polygon.forEach(([x, y], index) => index ? ctx.lineTo(rect.x + x * rect.w, rect.y + y * rect.h) : ctx.moveTo(rect.x + x * rect.w, rect.y + y * rect.h));
      ctx.closePath();
      ctx.fillStyle = `${color}1c`;
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash(zone.type === "restricted" ? [] : [7, 5]);
      ctx.stroke();
      ctx.setLineDash([]);
      const top = zone.polygon.reduce((point, current) => current[1] < point[1] ? current : point);
      label(ctx, zone.name, rect.x + top[0] * rect.w, rect.y + top[1] * rect.h + 22, color);
    }
  }
  if (!message) return;
  for (const person of message.people) {
    const target = box(person.bbox, rect);
    const severity = person.violations.reduce((worst, violation) => {
      const current = EVENT_SEVERITY[violation];
      if (!worst || (current === "high" || (current === "medium" && worst === "low"))) return current;
      return worst;
    }, "low" as "low" | "medium" | "high");
    const color = person.violations.length ? SEVERITY_COLOR[severity] : "#6fc59d";
    ctx.strokeStyle = color;
    ctx.lineWidth = person.violations.length ? 3 : 1.5;
    roundedRect(ctx, target.x, target.y, target.w, target.h, 3);
    ctx.stroke();
    if (person.violations.length) label(ctx, person.violations.map((item) => EVENT_LABEL[item]).join(" · "), target.x, target.y, color);
  }
  for (const object of message.objects) {
    if (object.cls !== "fire" && object.cls !== "smoke" && !object.cls.startsWith("no_")) continue;
    const target = box(object.bbox, rect);
    const color = object.cls === "fire" || object.cls === "smoke" ? SEVERITY_COLOR.high : SEVERITY_COLOR.medium;
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.strokeRect(target.x, target.y, target.w, target.h);
    label(ctx, object.cls === "fire" ? "Огонь" : object.cls === "smoke" ? "Дым" : object.cls.replace("no_", "Нет "), target.x, target.y, color);
  }
}
