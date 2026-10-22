import type { Point } from "../types";
import type { Rect } from "./geometry";
import { toNormalized } from "./geometry";

export interface PolygonDraft {
  points: Point[];
  closed: boolean;
}

export const EMPTY_DRAFT: PolygonDraft = { points: [], closed: false };
export const HANDLE_RADIUS = 9;

export function hitVertex(points: Point[], px: number, py: number, rect: Rect, radius = HANDLE_RADIUS): number {
  let best = -1;
  let bestDist = radius;
  points.forEach(([x, y], i) => {
    const d = Math.hypot(rect.x + x * rect.w - px, rect.y + y * rect.h - py);
    if (d <= bestDist) {
      best = i;
      bestDist = d;
    }
  });
  return best;
}

export function clickDraft(draft: PolygonDraft, px: number, py: number, rect: Rect): PolygonDraft {
  if (draft.closed) return draft;
  if (draft.points.length >= 3 && hitVertex(draft.points, px, py, rect) === 0) return { ...draft, closed: true };
  return { ...draft, points: [...draft.points, toNormalized(px, py, rect)] };
}

export function moveVertex(draft: PolygonDraft, index: number, px: number, py: number, rect: Rect): PolygonDraft {
  return { ...draft, points: draft.points.map((p, i) => (i === index ? toNormalized(px, py, rect) : p)) };
}

export function removeVertex(draft: PolygonDraft, index: number): PolygonDraft {
  if (draft.closed && draft.points.length <= 3) return draft;
  return { ...draft, points: draft.points.filter((_, i) => i !== index) };
}

export function closeDraft(draft: PolygonDraft): PolygonDraft {
  return draft.points.length >= 3 ? { ...draft, closed: true } : draft;
}

export function isComplete(draft: PolygonDraft): boolean {
  return draft.closed && draft.points.length >= 3;
}

export function toPolygon(draft: PolygonDraft): Point[] {
  return draft.points.map(([x, y]) => [Math.round(x * 1e4) / 1e4, Math.round(y * 1e4) / 1e4]);  // 4 знака как в бэке
}
