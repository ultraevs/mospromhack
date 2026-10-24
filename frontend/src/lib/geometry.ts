import type { BBox } from "../types";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function videoRect(containerW: number, containerH: number, videoW: number, videoH: number): Rect {
  if (!containerW || !containerH || !videoW || !videoH) return { x: 0, y: 0, w: containerW, h: containerH };
  const scale = Math.min(containerW / videoW, containerH / videoH);
  const w = videoW * scale;
  const h = videoH * scale;
  return { x: (containerW - w) / 2, y: (containerH - h) / 2, w, h };
}

export function toCanvas(bbox: BBox, rect: Rect): Rect {
  return { x: rect.x + bbox[0] * rect.w, y: rect.y + bbox[1] * rect.h, w: bbox[2] * rect.w, h: bbox[3] * rect.h };
}

export function toNormalized(px: number, py: number, rect: Rect): [number, number] {
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  return [clamp((px - rect.x) / rect.w), clamp((py - rect.y) / rect.h)];
}
