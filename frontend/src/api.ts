import type {
  AnalyticsSummary, AuthUser, Camera, ClientConfig, Delivery, EventPage, RuleSettings, SafetyEvent, Stats, TelegramSettings,
  TelegramStatus, UserRole, Webhook, WebhookInput, Zone, ZoneInput,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/me") window.dispatchEvent(new Event("auth-expired"));
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

type Query = Record<string, string | number | undefined>;

function query(params: Query): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  return q.toString();
}

export const api = {
  authMe: () => request<AuthUser>("/api/auth/me"),
  authLogin: (body: { login: string; password: string }) => request<AuthUser>("/api/auth/login", { method: "POST", body: JSON.stringify(body) }),
  authLogout: () => request<void>("/api/auth/logout", { method: "POST" }),
  authUsers: () => request<AuthUser[]>("/api/auth/users"),
  createAuthUser: (body: { login: string; display_name: string; role: UserRole; password: string }) => request<AuthUser>("/api/auth/users", { method: "POST", body: JSON.stringify(body) }),
  updateAuthUser: (id: number, body: Partial<{ display_name: string; role: UserRole; password: string; active: boolean }>) => request<AuthUser>(`/api/auth/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
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
  telegram: () => request<TelegramStatus>("/api/telegram"),
  setTelegramEnabled: (enabled: boolean) => request<TelegramStatus>("/api/telegram", { method: "PUT", body: JSON.stringify({ enabled }) }),
  telegramSettings: () => request<TelegramSettings>("/api/settings/telegram"),
  saveTelegramSettings: (body: TelegramSettings) =>
    request<TelegramSettings>("/api/settings/telegram", { method: "PUT", body: JSON.stringify(body) }),
  videos: () => request<{ name: string; size: number }[]>("/api/videos"),
  events: (params: Query = {}) => request<EventPage>(`/api/events?${query(params)}`),
  event: (id: string) => request<SafetyEvent>(`/api/events/${id}`),
  analytics: (params: Query = {}) => request<AnalyticsSummary>(`/api/analytics/summary?${query(params)}`, { cache: "no-store" }),
  webhooks: () => request<Webhook[]>("/api/webhooks"),
  createWebhook: (body: WebhookInput) => request<Webhook>("/api/webhooks", { method: "POST", body: JSON.stringify(body) }),
  updateWebhook: (id: number, body: Partial<WebhookInput>) =>
    request<Webhook>(`/api/webhooks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteWebhook: (id: number) => request<void>(`/api/webhooks/${id}`, { method: "DELETE" }),
  testWebhook: (id: number) => request<Delivery>(`/api/webhooks/${id}/test`, { method: "POST" }),
  deliveries: (id: number, limit = 30) => request<Delivery[]>(`/api/webhooks/${id}/deliveries?limit=${limit}`),
  reviewEvent: (id: string, review: SafetyEvent["review"]) =>
    request<SafetyEvent>(`/api/events/${id}`, { method: "PATCH", body: JSON.stringify({ review }) }),
  stats: () => request<Stats>("/api/stats"),
  clientConfig: () => request<ClientConfig>("/api/client-config"),
  health: () => request<import("./types").Health>("/api/health"),
};

export function wsUrl(path: string): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}

export function hlsUrl(cameraId: number, hlsPort: number): string {
  return `http://${location.hostname}:${hlsPort}/cam-${cameraId}/index.m3u8`;
}

export function whepUrl(cameraId: number, webrtcPort: number): string {
  return `http://${location.hostname}:${webrtcPort}/cam-${cameraId}/whep`;
}
