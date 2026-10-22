import { describe, expect, it } from "vitest";
import {
  activeCount, EMPTY_FILTERS, filtersFromParams, filtersToParams, formatDuration, localToIso, pageInfo, periodStart, toQuery,
} from "./journal";

describe("фильтры журнала в адресе", () => {
  it("читает фильтры и страницу, мусор в page даёт первую страницу", () => {
    const f = filtersFromParams(new URLSearchParams("camera_id=2&type=fire&review=false_positive&page=3&event=evt_1"));
    expect(f).toEqual({ ...EMPTY_FILTERS, camera_id: "2", type: "fire", review: "false_positive", page: 3 });
    expect(filtersFromParams(new URLSearchParams("page=abc")).page).toBe(0);
    expect(filtersFromParams(new URLSearchParams("page=-2")).page).toBe(0);
  });

  it("пишет только заполненное и сохраняет выбранное событие", () => {
    const f = { ...EMPTY_FILTERS, severity: "high", page: 2 };
    expect(filtersToParams(f, "evt_7").toString()).toBe("severity=high&page=2&event=evt_7");
    expect(filtersToParams(EMPTY_FILTERS).toString()).toBe("");
  });

  it("туда и обратно без потерь", () => {
    const f = { ...EMPTY_FILTERS, camera_id: "1", status: "open", from: "2026-10-04T08:00", to: "2026-10-04T20:00", page: 1 };
    expect(filtersFromParams(filtersToParams(f))).toEqual(f);
  });

  it("считает активные фильтры без страницы", () => {
    expect(activeCount(EMPTY_FILTERS)).toBe(0);
    expect(activeCount({ ...EMPTY_FILTERS, type: "fire", from: "2026-10-04T08:00", page: 4 })).toBe(2);
  });
});

describe("запрос к API", () => {
  it("переводит местное время в UTC и страницу в offset", () => {
    const q = toQuery({ ...EMPTY_FILTERS, type: "smoke", from: "2026-10-04T08:00", page: 2 }, 25);
    expect(q).toMatchObject({ type: "smoke", limit: 25, offset: 50, camera_id: undefined, to: undefined });
    expect(q.from).toBe(new Date("2026-10-04T08:00").toISOString());
  });

  it("кривую дату не отправляет", () => {
    expect(localToIso("")).toBeUndefined();
    expect(localToIso("вчера")).toBeUndefined();
  });
});

describe("страницы", () => {
  it("границы", () => {
    expect(pageInfo(0, 0, 25)).toEqual({ first: 0, last: 0, pages: 1, hasPrev: false, hasNext: false });
    expect(pageInfo(25, 0, 25)).toEqual({ first: 1, last: 25, pages: 1, hasPrev: false, hasNext: false });
    expect(pageInfo(26, 0, 25)).toEqual({ first: 1, last: 25, pages: 2, hasPrev: false, hasNext: true });
    expect(pageInfo(26, 1, 25)).toEqual({ first: 26, last: 26, pages: 2, hasPrev: true, hasNext: false });
  });
});

describe("длительность и период", () => {
  it("форматирует длительность", () => {
    expect(formatDuration("2026-10-04T12:00:00Z", null)).toBe("идёт");
    expect(formatDuration("2026-10-04T12:00:00Z", "2026-10-04T12:00:12Z")).toBe("12 с");
    expect(formatDuration("2026-10-04T12:00:00Z", "2026-10-04T12:03:05Z")).toBe("3 мин 05 с");
    expect(formatDuration("2026-10-04T12:00:00Z", "2026-10-04T13:02:30Z")).toBe("1 ч 02 мин");
  });

  it("считает начало периода", () => {
    const now = Date.parse("2026-10-04T12:00:00Z");
    expect(periodStart("day", now)).toBe("2026-10-03T12:00:00.000Z");
    expect(periodStart("week", now)).toBe("2026-09-27T12:00:00.000Z");
    expect(periodStart("all", now)).toBeUndefined();
  });
});
