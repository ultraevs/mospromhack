export const PAGE_SIZE = 25;

export interface JournalFilters {
  camera_id: string;
  type: string;
  severity: string;
  status: string;
  review: string;
  from: string;
  to: string;
  page: number;
}

export const EMPTY_FILTERS: JournalFilters = {
  camera_id: "", type: "", severity: "", status: "", review: "", from: "", to: "", page: 0,
};

const KEYS = ["camera_id", "type", "severity", "status", "review", "from", "to"] as const;

export function filtersFromParams(params: URLSearchParams): JournalFilters {
  const out = { ...EMPTY_FILTERS };
  for (const key of KEYS) out[key] = params.get(key) ?? "";
  const page = Number(params.get("page"));
  out.page = Number.isInteger(page) && page > 0 ? page : 0;
  return out;
}

export function filtersToParams(filters: JournalFilters, event?: string | null): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of KEYS) if (filters[key]) params.set(key, filters[key]);
  if (filters.page > 0) params.set("page", String(filters.page));
  if (event) params.set("event", event);
  return params;
}

export function localToIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function toQuery(filters: JournalFilters, pageSize = PAGE_SIZE): Record<string, string | number | undefined> {
  return {
    camera_id: filters.camera_id || undefined,
    type: filters.type || undefined,
    severity: filters.severity || undefined,
    status: filters.status || undefined,
    review: filters.review || undefined,
    from: localToIso(filters.from),
    to: localToIso(filters.to),
    limit: pageSize,
    offset: filters.page * pageSize,
  };
}

export function activeCount(filters: JournalFilters): number {
  return KEYS.filter((k) => filters[k]).length;
}

export interface PageInfo {
  first: number;
  last: number;
  pages: number;
  hasPrev: boolean;
  hasNext: boolean;
}

export function pageInfo(total: number, page: number, pageSize = PAGE_SIZE): PageInfo {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : page * pageSize + 1;
  return { first, last: Math.min(total, (page + 1) * pageSize), pages, hasPrev: page > 0, hasNext: page + 1 < pages };
}

export function formatDuration(startIso: string, endIso: string | null): string {
  if (!endIso) return "идёт";
  const s = Math.max(0, Math.round((new Date(endIso).getTime() - new Date(startIso).getTime()) / 1000));
  if (s < 60) return `${s} с`;
  if (s < 3600) return `${Math.floor(s / 60)} мин ${String(s % 60).padStart(2, "0")} с`;
  return `${Math.floor(s / 3600)} ч ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} мин`;
}

export function periodStart(period: string, now = Date.now()): string | undefined {
  const hours: Record<string, number> = { day: 24, week: 24 * 7, month: 24 * 30 };
  return hours[period] ? new Date(now - hours[period] * 3600_000).toISOString() : undefined;
}
