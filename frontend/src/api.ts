import type {
  AnalyticsSummary, AuthUser, Camera, Capacity, CameraInput, ClientConfig, Delivery, EventPage, Health, Review, RuleSettings, SafetyEvent,
  Shift, Stats, MaxSettings, MaxStatus, UserRole, Webhook, WebhookInput, Zone, ZoneInput,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // для FormData браузер сам ставит multipart с boundary
  const type: Record<string, string> = init?.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const res = await fetch(path, { credentials: "same-origin", ...init, headers: { ...type, ...init?.headers } });
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/me") window.dispatchEvent(new Event("auth-expired"));
    let detail = `Сервер ответил ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg?: string }) => d.msg ?? "").filter(Boolean).join("; ") || detail;
    } catch {
      // не json, оставляем код
    }
    throw new ApiError(detail, res.status);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

type Query = Record<string, string | number | undefined>;

function query(params: Query): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const api = {
  authMe: () => request<AuthUser>("/api/auth/me"),
  authLogin: (body: { login: string; password: string }) => request<AuthUser>("/api/auth/login", json("POST", body)),
  authLogout: () => request<void>("/api/auth/logout", { method: "POST" }),
  users: () => request<AuthUser[]>("/api/auth/users"),
  createUser: (body: { login: string; display_name: string; role: UserRole; password: string }) => request<AuthUser>("/api/auth/users", json("POST", body)),
  updateUser: (id: number, body: Partial<{ display_name: string; role: UserRole; password: string; active: boolean }>) =>
    request<AuthUser>(`/api/auth/users/${id}`, json("PATCH", body)),

  cameras: () => request<Camera[]>("/api/cameras"),
  createCamera: (body: CameraInput) => request<Camera>("/api/cameras", json("POST", body)),
  updateCamera: (id: number, body: Partial<CameraInput>) => request<Camera>(`/api/cameras/${id}`, json("PATCH", body)),
  deleteCamera: (id: number) => request<void>(`/api/cameras/${id}`, { method: "DELETE" }),
  videos: () => request<{ name: string; size: number }[]>("/api/videos"),
  images: () => request<{ name: string; count: number }[]>("/api/images"),
  upload: (files: File[]) => {
    const body = new FormData();
    for (const f of files) body.append("files", f);
    return request<{ source_type: "file" | "images"; source: string }>("/api/uploads", { method: "POST", body });
  },

  zones: (cameraId: number) => request<Zone[]>(`/api/cameras/${cameraId}/zones`),
  createZone: (cameraId: number, body: ZoneInput) => request<Zone>(`/api/cameras/${cameraId}/zones`, json("POST", body)),
  updateZone: (id: number, body: Partial<ZoneInput>) => request<Zone>(`/api/zones/${id}`, json("PATCH", body)),
  deleteZone: (id: number) => request<void>(`/api/zones/${id}`, { method: "DELETE" }),

  events: (params: Query = {}) => request<EventPage>(`/api/events${query(params)}`),
  // не fetch, а ссылка: браузер сам скачает файл с cookie
  eventsExportUrl: (params: Query = {}, format: "xlsx" | "csv" = "xlsx") => `/api/events/export${query({ ...params, format })}`,
  // печатный отчёт за смену, тоже ссылкой в новой вкладке
  eventsReportUrl: (params: Query = {}) => `/api/events/report${query(params)}`,
  event: (id: string) => request<SafetyEvent>(`/api/events/${id}`),
  reviewEvent: (id: string, review: Review, note?: string) =>
    request<SafetyEvent>(`/api/events/${id}`, json("PATCH", note ? { review, note } : { review })),
  eventsDatasetUrl: (params: Query = {}) => `/api/events/export/dataset${query(params)}`,

  analytics: (params: Query = {}) => request<AnalyticsSummary>(`/api/analytics/summary${query(params)}`, { cache: "no-store" }),
  stats: () => request<Stats>("/api/stats"),
  health: () => request<Health>("/api/health"),
  clientConfig: () => request<ClientConfig>("/api/client-config"),

  rules: () => request<RuleSettings>("/api/settings/rules"),
  saveRules: (body: RuleSettings) => request<RuleSettings>("/api/settings/rules", json("PUT", body)),
  max: () => request<MaxStatus>("/api/max"),
  setMaxEnabled: (enabled: boolean) => request<MaxStatus>("/api/max", json("PUT", { enabled })),
  maxSettings: () => request<MaxSettings>("/api/settings/max"),
  saveMaxSettings: (body: MaxSettings) => request<MaxSettings>("/api/settings/max", json("PUT", body)),
  shifts: () => request<{ shifts: Shift[] }>("/api/settings/shifts").then((r) => r.shifts),
  saveShifts: (shifts: Shift[]) => request<{ shifts: Shift[] }>("/api/settings/shifts", json("PUT", { shifts })).then((r) => r.shifts),
  capacity: (params: Query) => request<Capacity>(`/api/capacity${query(params)}`, { cache: "no-store" }),

  webhooks: () => request<Webhook[]>("/api/webhooks"),
  createWebhook: (body: WebhookInput) => request<Webhook>("/api/webhooks", json("POST", body)),
  updateWebhook: (id: number, body: Partial<WebhookInput>) => request<Webhook>(`/api/webhooks/${id}`, json("PATCH", body)),
  deleteWebhook: (id: number) => request<void>(`/api/webhooks/${id}`, { method: "DELETE" }),
  testWebhook: (id: number) => request<Delivery>(`/api/webhooks/${id}/test`, { method: "POST" }),
  deliveries: (id: number, limit = 30) => request<Delivery[]>(`/api/webhooks/${id}/deliveries?limit=${limit}`),
};

export function errorText(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

export function wsUrl(path: string): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}

export function hlsUrl(cameraId: number, hlsPort: number): string {
  return `http://${location.hostname}:${hlsPort}/cam-${cameraId}/index.m3u8`;
}

export function whepUrl(cameraId: number, webrtcPort: number): string {
  return `http://${location.hostname}:${webrtcPort}/cam-${cameraId}/whep`;
}
