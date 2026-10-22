import { applyEventMessage } from "../hooks/useEvents";
import type { DetectionsMessage, PersonBox, SafetyEvent } from "../types";
import { DEFAULT_LAYERS, drawDetections, isStale, personColor, personLabel, worstSeverity } from "./overlay";

function mockCtx() {
  const calls: { op: string; args: number[] | [string, number, number]; style: string }[] = [];
  const ctx = {
    lineWidth: 1,
    strokeStyle: "",
    fillStyle: "",
    font: "",
    measureText: (t: string) => ({ width: t.length * 7 }),
    strokeRect(...args: number[]) { calls.push({ op: "strokeRect", args, style: this.strokeStyle }); },
    fillRect(...args: number[]) { calls.push({ op: "fillRect", args, style: this.fillStyle }); },
    fillText(text: string, x: number, y: number) { calls.push({ op: "fillText", args: [text, x, y], style: this.fillStyle }); },
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}

const person = (over: Partial<PersonBox> = {}): PersonBox => ({
  track_id: 17, bbox: [0.5, 0.2, 0.25, 0.4], conf: 0.83, ppe: {}, zones: [], violations: [], ...over,
});

const message = (over: Partial<DetectionsMessage> = {}): DetectionsMessage => ({
  type: "detections", camera_id: 1, ts: 1_000_000, frame: { w: 1920, h: 1080 }, people: [], objects: [], ...over,
});

const RECT = { x: 0, y: 175, w: 800, h: 450 };

describe("drawDetections", () => {
  it("рисует рамку человека в координатах canvas с подписью нарушения", () => {
    const { ctx, calls } = mockCtx();
    drawDetections(ctx, message({ people: [person({ violations: ["no_helmet"], ppe: { helmet: "violation" } })] }), RECT, DEFAULT_LAYERS);
    const box = calls.find((c) => c.op === "strokeRect")!;
    expect(box.args).toEqual([400, 265, 200, 180]);
    expect(box.style).toBe("#f59e3b");
    expect(calls.find((c) => c.op === "fillText")!.args[0]).toBe("без каски");
  });

  it("человек без нарушений рисуется без подписи", () => {
    const { ctx, calls } = mockCtx();
    drawDetections(ctx, message({ people: [person({ ppe: { helmet: "ok" } })] }), RECT, DEFAULT_LAYERS);
    expect(calls.filter((c) => c.op === "strokeRect")).toHaveLength(1);
    expect(calls.find((c) => c.op === "strokeRect")!.style).toBe("#3fb97f");
    expect(calls.some((c) => c.op === "fillText")).toBe(false);
  });

  it("мелкие рамки СИЗ по умолчанию скрыты и включаются слоем", () => {
    const msg = message({ objects: [{ cls: "no_helmet", bbox: [0.1, 0.1, 0.05, 0.05], conf: 0.47, track_id: 17 }] });
    const hidden = mockCtx();
    drawDetections(hidden.ctx, msg, RECT, DEFAULT_LAYERS);
    expect(hidden.calls).toHaveLength(0);
    const shown = mockCtx();
    drawDetections(shown.ctx, msg, RECT, { ...DEFAULT_LAYERS, ppe: true });
    expect(shown.calls.filter((c) => c.op === "strokeRect")).toHaveLength(1);
  });

  it("огонь и дым видны всегда", () => {
    const { ctx, calls } = mockCtx();
    drawDetections(ctx, message({ objects: [{ cls: "fire", bbox: [0.7, 0.5, 0.1, 0.12], conf: 0.55, track_id: null }] }), RECT, DEFAULT_LAYERS);
    expect(calls.find((c) => c.op === "strokeRect")!.style).toBe("#f0524f");
  });

  it("слой людей и слой подписей выключаются", () => {
    const msg = message({ people: [person({ violations: ["no_vest"] })] });
    const noPeople = mockCtx();
    drawDetections(noPeople.ctx, msg, RECT, { ...DEFAULT_LAYERS, people: false });
    expect(noPeople.calls).toHaveLength(0);
    const noLabels = mockCtx();
    drawDetections(noLabels.ctx, msg, RECT, { ...DEFAULT_LAYERS, labels: false });
    expect(noLabels.calls.map((c) => c.op)).toEqual(["strokeRect"]);
  });
});

describe("статус человека", () => {
  it("цвет берётся по худшему нарушению", () => {
    const p = person({ violations: ["no_vest", "zone_intrusion", "no_helmet"] });
    expect(worstSeverity(p)).toBe("high");
    expect(personColor(p)).toBe("#f0524f");
    expect(personLabel(p)).toBe("без жилета, в запретной зоне, без каски");
  });
  it("неизвестный статус не красится в зелёный", () => {
    expect(personColor(person({ ppe: { helmet: "unknown" } }))).toBe("#9aa5b1");
    expect(personColor(person())).toBe("#9aa5b1");
    expect(personColor(person({ ppe: { helmet: "ok", vest: "not_required" } }))).toBe("#3fb97f");
  });
});

describe("isStale", () => {
  it("рамки старше секунды не рисуются", () => {
    expect(isStale(message({ ts: 1000 }), 1900)).toBe(false);
    expect(isStale(message({ ts: 1000 }), 2001)).toBe(true);
  });
  it("задержка рамок сдвигает границу", () => {
    expect(isStale(message({ ts: 1000 }), 2200, 300)).toBe(false);
    expect(isStale(message({ ts: 1000 }), 2301, 300)).toBe(true);
  });
});

describe("applyEventMessage", () => {
  const ev = (id: string, over: Partial<SafetyEvent> = {}) => ({ id, status: "open", ...over }) as SafetyEvent;
  it("новое событие встаёт первым, список ограничен", () => {
    const list = applyEventMessage([ev("a"), ev("b")], { type: "event_created", event: ev("c") }, 2);
    expect(list.map((e) => e.id)).toEqual(["c", "a"]);
  });
  it("обновление заменяет событие на месте и не добавляет незнакомое", () => {
    const list = [ev("a"), ev("b")];
    expect(applyEventMessage(list, { type: "event_updated", event: ev("b", { status: "closed" }) })[1].status).toBe("closed");
    expect(applyEventMessage(list, { type: "event_updated", event: ev("zzz") })).toHaveLength(2);
  });
});
