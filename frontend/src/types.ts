// то же что shared/schemas.py. рамки [x, y, w, h] в долях кадра
export type BBox = [number, number, number, number];
export type EventType = "no_helmet" | "no_mask" | "no_vest" | "zone_intrusion" | "smoking" | "fire" | "smoke"
  | "running" | "vehicle_proximity" | "fall" | "lying_immobile";
export type Severity = "low" | "medium" | "high";
export type Review = "none" | "confirmed" | "false_positive" | "forwarded";
export type PpeItem = "helmet" | "mask" | "vest";
export type Point = [number, number];
export type ZoneType = "restricted" | "ppe_required" | "flammable";
export type UserRole = "admin" | "viewer";

export interface Zone {
  id: number;
  camera_id: number;
  name: string;
  type: ZoneType;
  polygon: Point[];
  required_ppe: PpeItem[];
}

export type ZoneInput = Omit<Zone, "id" | "camera_id">;

export interface FractionRule { window_s: number; min_observations: number; fraction: number }
export interface RuleSettings {
  no_helmet: FractionRule;
  no_mask: FractionRule;
  no_vest: { window_s: number; min_observations: number; max_fraction: number; min_person_height_px: number };
  zone_intrusion: { window_s: number; fraction: number; min_observations: number };
  smoking: { k: number; m: number; upper_fraction: number; everywhere: boolean };
  fire: { k: number; m: number };
  smoke: { k: number; m: number };
  running: { window_s: number; min_observations: number; fraction: number; speed_heights_s: number };
  vehicle_proximity: { window_s: number; min_observations: number; fraction: number; distance_heights: number };
  fall: { window_s: number; min_observations: number; upright_ratio: number; lying_ratio: number; drop_heights: number };
  lying_immobile: { window_s: number; min_observations: number; fraction: number; lying_ratio: number; max_speed: number };
  lifecycle: {
    close_after_s: number; cooldown_s: number; retrack_iou: number; retrack_window_s: number;
    camera_cooldown_s: number; track_lost_s: number;
  };
}

export interface TelegramSettings {
  instant_min_severity: Severity;
  min_interval_s: number;
  summary_interval_s: number;
  repeat_interval_s: number;
}

export interface TelegramStatus {
  enabled: boolean;
  reason: "switched_off" | "no_token" | "invalid_token" | null;
  running: boolean;
  username: string | null;
  error: string | null;
  subscribers: number;
  sent: number;
  errors: number;
  pending: number;
}

export type PpeStatus = "ok" | "violation" | "unknown" | "not_required";

export interface SafetyEvent {
  id: string;
  type: EventType;
  severity: Severity;
  status: "open" | "closed";
  camera_id: number;
  camera_name: string;
  zone_id: number | null;
  zone_name: string | null;
  track_id: number | null;
  started_at: string;
  ended_at: string | null;
  confidence: number;
  bbox: BBox | null;
  snapshot_url: string | null;
  snapshot_clean_url: string | null;
  clip_url: string | null;
  review: Review;
  details: Record<string, unknown>;
}

export interface EventMessage {
  type: "event_created" | "event_updated";
  event: SafetyEvent;
}

export interface PersonBox {
  track_id: number;
  bbox: BBox;
  conf: number;
  ppe: Partial<Record<PpeItem, PpeStatus>>;
  zones: number[];
  violations: EventType[];
}

export interface ObjectBox {
  cls: string;
  bbox: BBox;
  conf: number;
  track_id: number | null;
}

export interface DetectionsMessage {
  type: "detections";
  camera_id: number;
  ts: number;
  frame: { w: number; h: number };
  people: PersonBox[];
  objects: ObjectBox[];
}

export interface Camera {
  id: number;
  name: string;
  source_type: "rtsp" | "file" | "images";
  source: string;
  required_ppe: PpeItem[];
  enabled: boolean;
  status: "online" | "offline";
  open_events: number;
}

export type CameraInput = Pick<Camera, "name" | "source_type" | "source" | "required_ppe" | "enabled">;

export interface AuthUser {
  id: number;
  login: string;
  display_name: string;
  role: UserRole;
  active: boolean;
  created_at: string;
}

export interface EventPage {
  items: SafetyEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface LatencySummary { avg_ms: number; p95_ms: number; events: number }

export interface Stats {
  worker_alive: boolean;
  detector?: string;
  device?: string;
  warnings?: string[];
  streams?: number;
  crops_per_s?: number;
  crop_pass?: { cigarette: boolean; head: boolean };
  target_fps?: Record<string, number>;
  actual_fps?: Record<string, number>;
  inference?: Record<string, { avg_ms: number; p95_ms: number; batch: number }>;
  cameras?: Record<string, { online: boolean; error: string | null; read_fps: number; proc_fps: number }>;
  clips?: { built: number; failed: number; pending: number };
  latency?: { capture_to_event: LatencySummary | null; capture_to_telegram?: LatencySummary | null };
}

export interface SourceHealth {
  kind?: string;
  publishing?: boolean;
  ffmpeg_running?: boolean;
  online?: boolean;
  error?: string | null;
}

export interface Health {
  status: "ok" | "degraded" | string;
  problems: string[];
  backend?: { ok: boolean };
  mediamtx?: { ok: boolean; error?: string | null };
  worker?: { ok: boolean; detector?: string | null; device?: string | null };
  sources?: Record<string, SourceHealth>;
}

export interface Webhook {
  id: number;
  url: string;
  secret: string;
  event_types: EventType[];
  min_severity: Severity;
  send_closed: boolean;
  enabled: boolean;
}

export type WebhookInput = Omit<Webhook, "id">;

export interface Delivery {
  id: number;
  webhook_id: number;
  event_id: string;
  action: string;
  attempt: number;
  status_code: number | null;
  ok: boolean;
  error: string | null;
  duration_ms: number;
  created_at: string;
}

export interface ReviewCounts {
  total: number;
  confirmed: number;
  false_positive: number;
  unreviewed: number;
  forwarded: number;
  false_share: number | null;
}

export interface AnalyticsSummary extends ReviewCounts {
  by_type: ({ type: EventType } & ReviewCounts)[];
  by_severity: { severity: Severity; total: number }[];
  by_camera: { camera_id: number; camera_name: string; total: number }[];
  by_zone: { zone_id: number; zone_name: string | null; camera_name: string; total: number }[];
  without_zone: number;
  by_hour: { hour: number; total: number }[];
  by_day: { day: string; total: number }[];
}

export interface ClientConfig {
  video: "webrtc" | "hls";
  webrtc_port: number;
  hls_port: number;
  overlay_delay_ms: number;
}

export const EVENT_LABEL: Record<EventType, string> = {
  no_helmet: "Без каски",
  no_mask: "Без маски",
  no_vest: "Без жилета",
  zone_intrusion: "В запретной зоне",
  smoking: "Курение",
  fire: "Огонь",
  smoke: "Дым",
  running: "Бег",
  vehicle_proximity: "Рядом с техникой",
  fall: "Падение",
  lying_immobile: "Лежит неподвижно",
};

export const EVENT_TYPES = Object.keys(EVENT_LABEL) as EventType[];

export const REVIEW_LABEL: Record<Review, string> = {
  none: "Не разобрано",
  confirmed: "Подтверждено",
  false_positive: "Ложное",
  forwarded: "Передано мастеру",
};

export const SEVERITY_LABEL: Record<Severity, string> = { high: "Высокая", medium: "Средняя", low: "Низкая" };
export const SEVERITY_COLOR: Record<Severity, string> = { high: "#e5483b", medium: "#f08c2a", low: "#f4c430" };
export const SEVERITY_RANK: Record<Severity, number> = { low: 0, medium: 1, high: 2 };

export const EVENT_SEVERITY: Record<EventType, Severity> = {
  fire: "high", smoke: "high", zone_intrusion: "high",
  vehicle_proximity: "high", fall: "high", lying_immobile: "high", running: "medium",
  no_helmet: "medium", no_mask: "medium", smoking: "medium",
  no_vest: "low",
};

export const PPE_ITEMS: PpeItem[] = ["helmet", "mask", "vest"];
export const PPE_LABEL: Record<PpeItem, string> = { helmet: "Каска", mask: "Маска", vest: "Жилет" };

export const ZONE_TYPES: ZoneType[] = ["restricted", "ppe_required", "flammable"];
export const ZONE_TYPE_LABEL: Record<ZoneType, string> = {
  restricted: "Вход запрещён",
  ppe_required: "Обязательны СИЗ",
  flammable: "Горючие материалы",
};
export const ZONE_COLOR: Record<ZoneType, string> = { restricted: "#e5483b", ppe_required: "#3b82d0", flammable: "#f08c2a" };
