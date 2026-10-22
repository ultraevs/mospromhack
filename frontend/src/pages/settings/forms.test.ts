import { describe, expect, it } from "vitest";
import type { Delivery } from "../../types";
import { draftError } from "./CamerasTab";
import { deliveryText, webhookError } from "./WebhooksTab";

const camera = { name: "Цех", source_type: "file" as const, source: "a.mp4", required_ppe: [], enabled: true };
const hook = { url: "http://127.0.0.1:9100/hook", secret: "s", event_types: [], min_severity: "low" as const, send_closed: false, enabled: true };
const delivery: Delivery = { id: 1, webhook_id: 1, event_id: "evt_1", action: "opened", attempt: 1, status_code: 200, ok: true,
                             error: null, duration_ms: 8, created_at: "2026-10-04T12:00:00Z" };

describe("форма камеры", () => {
  it("требует название и источник", () => {
    expect(draftError(camera)).toBe("");
    expect(draftError({ ...camera, name: "  " })).toMatch(/название/);
    expect(draftError({ ...camera, source: "" })).toMatch(/видеофайл/);
  });

  it("проверяет адрес RTSP", () => {
    expect(draftError({ ...camera, source_type: "rtsp", source: "rtsp://10.0.0.5:554/s1" })).toBe("");
    expect(draftError({ ...camera, source_type: "rtsp", source: "RTSPS://cam/s" })).toBe("");
    expect(draftError({ ...camera, source_type: "rtsp", source: "http://cam/s" })).toMatch(/rtsp:\/\//);
    expect(draftError({ ...camera, source_type: "rtsp", source: "" })).toMatch(/rtsp:\/\//);
  });
});

describe("форма вебхука", () => {
  it("требует адрес http(s) и секрет", () => {
    expect(webhookError(hook)).toBe("");
    expect(webhookError({ ...hook, url: "example.com/hook" })).toMatch(/http/);
    expect(webhookError({ ...hook, secret: " " })).toMatch(/секрет/);
  });

  it("описывает доставку словами", () => {
    expect(deliveryText(delivery)).toBe("✔ доставлено, ответ 200, 8 мс");
    expect(deliveryText({ ...delivery, ok: false, status_code: 500 })).toBe("✖ не доставлено: ответ 500, 8 мс");
    expect(deliveryText({ ...delivery, ok: false, status_code: null, error: "ConnectError" })).toBe("✖ не доставлено: ConnectError, 8 мс");
  });
});
