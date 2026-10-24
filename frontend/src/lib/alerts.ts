import type { EventMessage, SafetyEvent } from "../types";

export interface AlertPrefs { sound: boolean; banner: boolean }

const KEY = "alert-prefs";
export const DEFAULT_PREFS: AlertPrefs = { sound: true, banner: true };
const MAX_SHOWN = 3;

export function loadPrefs(): AlertPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "null");
    return { sound: raw?.sound !== false, banner: raw?.banner !== false };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export function savePrefs(p: AlertPrefs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // приватный режим — живем с дефолтами
  }
  window.dispatchEvent(new Event("alert-prefs"));
}

export const isCritical = (e: SafetyEvent) => e.severity === "high" && e.review === "none";

// новые сверху, не больше трёх; снятые разбором/скрытые уходят
export function applyMessage(list: SafetyEvent[], m: EventMessage, hidden: ReadonlySet<string>): SafetyEvent[] {
  const e = m.event;
  const rest = list.filter((x) => x.id !== e.id);
  if (e.severity !== "high" || e.review !== "none" || hidden.has(e.id)) return rest;
  if (m.type === "event_updated") {
    const had = list.some((x) => x.id === e.id);
    return had ? list.map((x) => (x.id === e.id ? e : x)) : list;
  }
  return [e, ...rest].slice(0, MAX_SHOWN);
}

type Listener = (m: EventMessage) => void;
const listeners = new Set<Listener>();
export const emitLive = (m: EventMessage) => listeners.forEach((l) => l(m));
export function onLive(l: Listener) {
  listeners.add(l);
  return () => void listeners.delete(l);
}

let ctx: AudioContext | null = null;

export function unlockAudio() {
  try {
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
  } catch {
    ctx = null;
  }
}

export function beep() {
  try {
    if (!ctx || ctx.state !== "running") return;
    const t0 = ctx.currentTime;
    for (const off of [0, 0.28]) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "square";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, t0 + off);
      gain.gain.exponentialRampToValueAtTime(0.18, t0 + off + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + off + 0.2);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t0 + off);
      osc.stop(t0 + off + 0.22);
    }
  } catch {
    // звук не критичен
  }
}
