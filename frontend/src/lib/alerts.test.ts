import { describe, expect, it } from "vitest";
import type { EventMessage, SafetyEvent } from "../types";
import { applyMessage, loadPrefs } from "./alerts";

const ev = (id: string, o: Partial<SafetyEvent> = {}): SafetyEvent => ({
  id, type: "fall", severity: "high", status: "open", camera_id: 1, camera_name: "К1", zone_id: null, zone_name: null,
  track_id: null, started_at: "2026-01-01T10:00:00Z", ended_at: null, confidence: 0.9, bbox: null,
  snapshot_url: null, snapshot_clean_url: null, clip_url: null, clip_anon_url: null, review: "none",
  reviewed_at: null, reviewed_by: null, review_note: null, details: {}, ...o,
});
const msg = (type: EventMessage["type"], event: SafetyEvent): EventMessage => ({ type, event });
const none = new Set<string>();

describe("alerts", () => {
  it("берёт только high, новые сверху, максимум три", () => {
    let l: SafetyEvent[] = [];
    l = applyMessage(l, msg("event_created", ev("a", { severity: "medium" })), none);
    expect(l).toHaveLength(0);
    for (const id of ["1", "2", "3", "4"]) l = applyMessage(l, msg("event_created", ev(id)), none);
    expect(l.map((e) => e.id)).toEqual(["4", "3", "2"]);
  });

  it("убирает событие после разбора и не показывает скрытое", () => {
    let l = applyMessage([], msg("event_created", ev("1")), none);
    l = applyMessage(l, msg("event_updated", ev("1", { review: "confirmed" })), none);
    expect(l).toHaveLength(0);
    expect(applyMessage([], msg("event_created", ev("2")), new Set(["2"]))).toHaveLength(0);
  });

  it("event_updated не добавляет новое событие", () => {
    expect(applyMessage([], msg("event_updated", ev("1")), none)).toHaveLength(0);
  });

  it("без localStorage настройки по умолчанию включены", () => {
    expect(loadPrefs()).toEqual({ sound: true, banner: true });
  });
});
