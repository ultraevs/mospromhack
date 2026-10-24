import { describe, expect, it } from "vitest";
import type { Camera, DetectionsMessage, Health, PersonBox } from "../types";
import { camState } from "./cameras";
import { formatDuration, localDayEnd, localDayStart, plural } from "./format";
import { videoRect } from "./geometry";
import { isStale, takeSynced, worstSeverity } from "./overlay";
import { clickDraft, closeDraft, EMPTY_DRAFT, isComplete, moveVertex, removeVertex, toPolygon } from "./polygon";

const rect = { x: 0, y: 0, w: 100, h: 100 };

describe("polygon", () => {
  it("строит контур кликами и замыкается кликом по первой вершине", () => {
    let d = EMPTY_DRAFT;
    for (const [x, y] of [[10, 10], [90, 10], [90, 90]]) d = clickDraft(d, x, y, rect);
    expect(d.points).toHaveLength(3);
    expect(d.closed).toBe(false);
    d = clickDraft(d, 11, 11, rect);
    expect(d.closed).toBe(true);
    expect(isComplete(d)).toBe(true);
  });

  it("не замыкает меньше трёх вершин", () => {
    let d = clickDraft(EMPTY_DRAFT, 10, 10, rect);
    d = clickDraft(d, 50, 50, rect);
    expect(closeDraft(d).closed).toBe(false);
  });

  it("не даёт удалить вершину замкнутого треугольника", () => {
    const tri = { points: [[0, 0], [1, 0], [1, 1]] as [number, number][], closed: true };
    expect(removeVertex(tri, 0).points).toHaveLength(3);
  });

  it("двигает вершину и обрезает за пределами кадра", () => {
    const tri = { points: [[0, 0], [1, 0], [1, 1]] as [number, number][], closed: true };
    expect(moveVertex(tri, 0, 150, -20, rect).points[0]).toEqual([1, 0]);
  });

  it("округляет до 4 знаков как backend", () => {
    expect(toPolygon({ points: [[0.123456, 0.987654]], closed: false })).toEqual([[0.1235, 0.9877]]);
  });
});

describe("overlay", () => {
  const msg = (ts: number): DetectionsMessage => ({ type: "detections", camera_id: 1, ts, frame: { w: 1, h: 1 }, people: [], objects: [] });

  it("берёт рамки с учётом задержки видео и выкидывает старые", () => {
    const q = [msg(1000), msg(1500), msg(2000), msg(2500)];
    const got = takeSynced(q, 3000, 900);
    expect(got?.ts).toBe(2000);
    expect(q.map((m) => m.ts)).toEqual([2000, 2500]);
  });

  it("ничего не рисует, если все рамки новее задержки", () => {
    expect(takeSynced([msg(2900)], 3000, 500)).toBeUndefined();
  });

  it("считает рамку устаревшей после MAX_AGE плюс задержка", () => {
    expect(isStale(msg(0), 2000, 0)).toBe(false);
    expect(isStale(msg(0), 3000, 0)).toBe(true);
    expect(isStale(msg(0), 3000, 1000)).toBe(false);
  });

  it("худшая важность у человека", () => {
    const p = { violations: ["no_vest", "no_helmet"] } as PersonBox;
    expect(worstSeverity(p)).toBe("medium");
    expect(worstSeverity({ violations: [] } as unknown as PersonBox)).toBeNull();
  });
});

describe("cameras", () => {
  const cam = (over: Partial<Camera>): Camera => ({
    id: 4, name: "Цех", source_type: "file", source: "a.mp4", required_ppe: [], enabled: true, status: "offline", open_events: 0, ...over,
  });
  const health = { status: "degraded", problems: [], sources: { "4": { publishing: true } } } as Health;

  it("различает анализ, видео без анализа, нет сигнала и выключена", () => {
    expect(camState(cam({ status: "online" }), health)).toBe("live");
    expect(camState(cam({}), health)).toBe("video");
    expect(camState(cam({}), null)).toBe("down");
    expect(camState(cam({ enabled: false }), health)).toBe("off");
  });
});

describe("format", () => {
  it("склоняет", () => {
    expect([1, 2, 5, 11, 21, 22, 112].map((n) => plural(n, "камера", "камеры", "камер"))).toEqual(
      ["камера", "камеры", "камер", "камер", "камера", "камеры", "камер"]);
  });

  it("день из date-input — местные сутки", () => {
    const start = new Date(localDayStart("2026-10-24"));
    const end = new Date(localDayEnd("2026-10-24"));
    expect([start.getFullYear(), start.getMonth(), start.getDate(), start.getHours()]).toEqual([2026, 9, 24, 0]);
    expect([end.getDate(), end.getHours(), end.getMinutes()]).toEqual([24, 23, 59]);
  });

  it("длительность", () => {
    expect(formatDuration("2026-10-24T10:00:00Z", "2026-10-24T10:00:42Z")).toBe("42 с");
    expect(formatDuration("2026-10-24T10:00:00Z", "2026-10-24T11:05:00Z")).toBe("1 ч 5 мин");
  });
});

describe("geometry", () => {
  it("вписывает кадр 16:9 в квадрат с полями сверху и снизу", () => {
    expect(videoRect(160, 160, 1600, 900)).toEqual({ x: 0, y: 35, w: 160, h: 90 });
  });
});
