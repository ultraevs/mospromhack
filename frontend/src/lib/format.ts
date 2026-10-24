import { EVENT_LABEL, REVIEW_LABEL, SEVERITY_LABEL, type Review, type SafetyEvent, type Severity } from "../types";

export const formatTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";
export const formatDateTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";
export const formatPercent = (value: number | null | undefined) => value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
export const formatCount = (value: number) => new Intl.NumberFormat("ru-RU").format(value);
export const eventLabel = (type: SafetyEvent["type"] | string) => EVENT_LABEL[type as keyof typeof EVENT_LABEL] ?? "Неизвестный сигнал";
export const severityLabel = (value: Severity) => SEVERITY_LABEL[value];
export const reviewLabel = (value: Review) => REVIEW_LABEL[value];

export function mediaUrl(path: string | null | undefined) {
  if (!path) return null;
  return path.startsWith("http") ? path : path;
}

export function severityClass(value: Severity) { return `severity-${value}`; }
export function reviewClass(value: Review) { return `review-${value}`; }
