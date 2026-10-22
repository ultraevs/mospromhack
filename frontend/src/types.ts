// то же что shared/schemas.py. рамки [x, y, w, h] в долях кадра
export type BBox = [number, number, number, number];
export type EventType = "no_helmet" | "no_mask" | "no_vest" | "zone_intrusion" | "smoking" | "fire" | "smoke";
export type Severity = "low" | "medium" | "high";
export type Review = "none" | "confirmed" | "false_positive";
export type PpeItem = "helmet" | "mask" | "vest";
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
  source_type: "rtsp" | "file";
  source: string;
  required_ppe: PpeItem[];
  enabled: boolean;
  status: "online" | "offline";
  open_events: number;
}

export interface EventPage {
  items: SafetyEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface ClientConfig {
  webrtc_port: number;
  overlay_delay_ms: number;
}

export const EVENT_LABEL: Record<EventType, string> = {
  no_helmet: "без каски",
  no_mask: "без маски",
  no_vest: "без жилета",
  zone_intrusion: "в запретной зоне",
  smoking: "курение",
  fire: "огонь",
  smoke: "дым",
};

export const SEVERITY_LABEL: Record<Severity, string> = { high: "высокая", medium: "средняя", low: "низкая" };
export const SEVERITY_ICON: Record<Severity, string> = { high: "▲", medium: "◆", low: "●" };
export const SEVERITY_COLOR: Record<Severity, string> = { high: "#f0524f", medium: "#f59e3b", low: "#e8cf4a" };
export const EVENT_SEVERITY: Record<EventType, Severity> = {
  fire: "high", smoke: "high", zone_intrusion: "high",
  no_helmet: "medium", no_mask: "medium", smoking: "medium",
  no_vest: "low",
};
