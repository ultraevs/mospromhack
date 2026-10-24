import type { Camera, Health } from "../types";

export type CamState = "live" | "video" | "down" | "off";

export const CAM_STATE_LABEL: Record<CamState, string> = {
  live: "Анализ идёт",
  video: "Видео есть, анализа нет",
  down: "Нет сигнала",
  off: "Выключена",
};

// status приходит от воркера, а поток публикует backend: без воркера видео есть, а камера offline
export function camState(camera: Camera, health: Health | null): CamState {
  if (!camera.enabled) return "off";
  if (camera.status === "online") return "live";
  return health?.sources?.[String(camera.id)]?.publishing ? "video" : "down";
}
