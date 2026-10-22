import type { Camera, ClientConfig, EventPage, RuleSettings, SafetyEvent, Zone, ZoneInput } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
    }
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  cameras: () => request<Camera[]>("/api/cameras"),
  camera: (id: number) => request<Camera>(`/api/cameras/${id}`),
  createCamera: (body: Partial<Camera>) => request<Camera>("/api/cameras", { method: "POST", body: JSON.stringify(body) }),
  deleteCamera: (id: number) => request<void>(`/api/cameras/${id}`, { method: "DELETE" }),
  updateCamera: (id: number, body: Partial<Camera>) =>
    request<Camera>(`/api/cameras/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  zones: (cameraId: number) => request<Zone[]>(`/api/cameras/${cameraId}/zones`),
  createZone: (cameraId: number, body: ZoneInput) =>
    request<Zone>(`/api/cameras/${cameraId}/zones`, { method: "POST", body: JSON.stringify(body) }),
  updateZone: (id: number, body: Partial<ZoneInput>) =>
    request<Zone>(`/api/zones/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteZone: (id: number) => request<void>(`/api/zones/${id}`, { method: "DELETE" }),
  rules: () => request<RuleSettings>("/api/settings/rules"),
  saveRules: (body: RuleSettings) => request<RuleSettings>("/api/settings/rules", { method: "PUT", body: JSON.stringify(body) }),
  videos: () => request<{ name: string; size: number }[]>("/api/videos"),
  events: (params: Record<string, string | number | undefined> = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
    return request<EventPage>(`/api/events?${q}`);
  },
  reviewEvent: (id: string, review: SafetyEvent["review"]) =>
    request<SafetyEvent>(`/api/events/${id}`, { method: "PATCH", body: JSON.stringify({ review }) }),
  clientConfig: () => request<ClientConfig>("/api/client-config"),
  health: () => request<{ status: string; problems: string[] }>("/api/health"),
};

export function wsUrl(path: string): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}

export function whepUrl(cameraId: number, webrtcPort: number): string {
  return `http://${location.hostname}:${webrtcPort}/cam-${cameraId}/whep`;
}
