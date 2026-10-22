import { toCanvas, toNormalized, videoRect } from "./geometry";

describe("videoRect: положение видео при object-fit: contain", () => {
  it("широкое видео в квадратном контейнере: поля сверху и снизу", () => {
    expect(videoRect(800, 800, 1920, 1080)).toEqual({ x: 0, y: 175, w: 800, h: 450 });
  });
  it("квадратное видео в широком контейнере: поля слева и справа", () => {
    expect(videoRect(1000, 500, 640, 640)).toEqual({ x: 250, y: 0, w: 500, h: 500 });
  });
  it("совпадающие пропорции: без полей", () => {
    expect(videoRect(960, 540, 1920, 1080)).toEqual({ x: 0, y: 0, w: 960, h: 540 });
  });
  it("размер видео ещё неизвестен: весь контейнер", () => {
    expect(videoRect(800, 600, 0, 0)).toEqual({ x: 0, y: 0, w: 800, h: 600 });
  });
});

describe("пересчёт координат", () => {
  const rect = { x: 0, y: 175, w: 800, h: 450 };
  it("нормированная рамка -> пиксели canvas с учётом полей", () => {
    expect(toCanvas([0.5, 0.2, 0.25, 0.4], rect)).toEqual({ x: 400, y: 265, w: 200, h: 180 });
    expect(toCanvas([0, 0, 1, 1], rect)).toEqual(rect);
  });
  it("пиксели canvas -> доли кадра и обратно", () => {
    const [nx, ny] = toNormalized(400, 265, rect);
    expect(nx).toBeCloseTo(0.5);
    expect(ny).toBeCloseTo(0.2);
    const back = toCanvas([nx, ny, 0, 0], rect);
    expect(back.x).toBeCloseTo(400);
    expect(back.y).toBeCloseTo(265);
  });
  it("точка в поле вне видео прижимается к краю кадра", () => {
    expect(toNormalized(400, 10, rect)).toEqual([0.5, 0]);
    expect(toNormalized(9999, 9999, rect)).toEqual([1, 1]);
  });
});
