import type { EventMessage, SafetyEvent, Severity } from "../types";

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
let lastPlay = 0;
const GAP_MS = 1200; // пачка событий не превращается в пулемёт

// браузер не дает играть звук до первого клика - шапка показывает это
export const audioLocked = () => !ctx || ctx.state !== "running";

export function unlockAudio() {
  try {
    if (!ctx) {
      ctx = new AudioContext();
      ctx.onstatechange = () => window.dispatchEvent(new Event("audio-state"));
    }
    if (ctx.state === "suspended") void ctx.resume();
  } catch {
    ctx = null;
  }
}

// [частота, старт, длительность]: high - три резких выстрела, medium - два, low - один мягкий
const PATTERN: Record<Severity, { wave: OscillatorType; gain: number; shots: [number, number, number][] }> = {
  high: { wave: "square", gain: 0.22, shots: [[1180, 0, 0.11], [880, 0.15, 0.11], [1180, 0.3, 0.16]] },
  medium: { wave: "square", gain: 0.16, shots: [[880, 0, 0.16], [880, 0.26, 0.16]] },
  low: { wave: "sine", gain: 0.2, shots: [[660, 0, 0.22]] },
};

export function alarm(severity: Severity) {
  try {
    if (!ctx || ctx.state !== "running") return;
    if (Date.now() - lastPlay < GAP_MS) return;
    lastPlay = Date.now();
    const { wave, gain: peak, shots } = PATTERN[severity];
    const t0 = ctx.currentTime;
    for (const [freq, off, len] of shots) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = wave;
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t0 + off);
      gain.gain.exponentialRampToValueAtTime(peak, t0 + off + 0.008);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + off + len);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t0 + off);
      osc.stop(t0 + off + len + 0.02);
    }
  } catch {
    // звук не критичен
  }
}
