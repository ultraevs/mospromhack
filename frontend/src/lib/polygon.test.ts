import { clickDraft, closeDraft, EMPTY_DRAFT, hitVertex, isComplete, moveVertex, removeVertex, toPolygon } from "./polygon";
import type { PolygonDraft } from "./polygon";

const RECT = { x: 0, y: 175, w: 800, h: 450 };

function draw(clicks: [number, number][], draft: PolygonDraft = EMPTY_DRAFT): PolygonDraft {
  return clicks.reduce((d, [x, y]) => clickDraft(d, x, y, RECT), draft);
}

describe("рисование полигона кликами", () => {
  it("клики добавляют точки в долях кадра", () => {
    const d = draw([[400, 400], [800, 175]]);
    expect(d.closed).toBe(false);
    expect(d.points).toEqual([[0.5, 0.5], [1, 0]]);
  });

  it("клик в поле вне видео прижимается к краю кадра", () => {
    expect(draw([[400, 20]]).points).toEqual([[0.5, 0]]);
  });

  it("клик по первой вершине замыкает контур, когда точек три и больше", () => {
    const three = draw([[100, 200], [300, 200], [200, 400]]);
    const closed = clickDraft(three, 103, 198, RECT);
    expect(closed.closed).toBe(true);
    expect(closed.points).toHaveLength(3);
    expect(isComplete(closed)).toBe(true);
  });

  it("клик по первой вершине при двух точках добавляет точку, а не замыкает", () => {
    const d = clickDraft(draw([[100, 200], [300, 200]]), 100, 200, RECT);
    expect(d.closed).toBe(false);
    expect(d.points).toHaveLength(3);
  });

  it("после замыкания клики точек не добавляют", () => {
    const closed = closeDraft(draw([[100, 200], [300, 200], [200, 400]]));
    expect(clickDraft(closed, 500, 500, RECT)).toBe(closed);
  });

  it("closeDraft не замыкает контур из двух точек", () => {
    const d = closeDraft(draw([[100, 200], [300, 200]]));
    expect(d.closed).toBe(false);
    expect(isComplete(d)).toBe(false);
  });
});

describe("вершины", () => {
  const closed = closeDraft(draw([[100, 200], [300, 200], [300, 400], [100, 400]]));

  it("hitVertex находит вершину в радиусе и ближайшую из двух", () => {
    expect(hitVertex(closed.points, 104, 204, RECT)).toBe(0);
    expect(hitVertex(closed.points, 120, 220, RECT)).toBe(-1);
    const near = draw([[100, 200], [108, 200]]);
    expect(hitVertex(near.points, 106, 200, RECT)).toBe(1);
  });

  it("перетаскивание меняет одну вершину и не выходит за кадр", () => {
    const moved = moveVertex(closed, 2, 400, 625, RECT);
    expect(moved.points[2]).toEqual([0.5, 1]);
    expect(moved.points[0]).toEqual(closed.points[0]);
    expect(moveVertex(closed, 0, -50, 0, RECT).points[0]).toEqual([0, 0]);
    expect(closed.points[2]).not.toEqual([0.5, 1]);
  });

  it("удаление вершины; у замкнутого контура остаётся минимум три", () => {
    const three = removeVertex(closed, 1);
    expect(three.points).toHaveLength(3);
    expect(removeVertex(three, 0)).toBe(three);
    const open = draw([[100, 200], [300, 200]]);
    expect(removeVertex(open, 0).points).toHaveLength(1);
  });

  it("toPolygon округляет до 4 знаков", () => {
    expect(toPolygon({ points: [[0.123456, 0.999999]], closed: true })).toEqual([[0.1235, 1]]);
  });
});
