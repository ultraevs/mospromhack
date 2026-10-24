import { EVENT_LABEL, type EventType } from "../types";

// знаки по ГОСТ 12.4.026 / ISO 7010: синий круг предписание, красный запрет, желтый треугольник предупреждение
type Family = "mandatory" | "prohibition" | "warning";

const FAMILY: Record<EventType, Family> = {
  no_helmet: "mandatory", no_mask: "mandatory", no_vest: "mandatory",
  zone_intrusion: "prohibition", smoking: "prohibition", running: "prohibition",
  fire: "warning", smoke: "warning", vehicle_proximity: "warning", fall: "warning", lying_immobile: "warning",
};

const GLYPH: Record<EventType, string> = {
  no_helmet: "M7 15a5 5 0 0 1 10 0M6 15.5h12M12 10v-2",
  no_mask: "M7.5 10.5h9v2.5a4.5 4.5 0 0 1-9 0zM7.5 11 5 9.5M16.5 11 19 9.5",
  no_vest: "M9 6 6.5 9v9H10v-7l2 2 2-2v7h3.5V9L15 6l-3 3z",
  zone_intrusion: "M12 6.5a1.5 1.5 0 1 0 0 .01M12 9v5m0 0-2 4m2-4 2 4m-4.5-7 2.5-1 2.5 1",
  smoking: "M6 13.5h10v2H6zM17.5 13.5v2M16 11c0-1.5 1.5-1.5 1.5-3",
  running: "M14 6.5a1.5 1.5 0 1 0 0 .01M8 11l3-2 3 1 2 3M11 9l-1 5-3 3m3-3 3 1 1 3",
  fire: "M12 8c.8 2 3 2.8 3 5.5a3 3 0 0 1-6 0c0-1.3.6-2 1.3-2.7 0 1.2.7 1.9 1.4 1.9 0-1.8-.6-3 .3-4.7z",
  smoke: "M8.5 15c1-1 2-1 3 0s2 1 3 0M8.5 12c1-1 2-1 3 0s2 1 3 0M10 9c.7-.6 1.3-.6 2 0",
  vehicle_proximity: "M7 15V10h4l1.5 3H15v2M7 15h8M8.5 16.5a1 1 0 1 0 0 .01M13.5 16.5a1 1 0 1 0 0 .01M15 9v6",
  fall: "M14.5 8a1.3 1.3 0 1 0 0 .01M13.5 10l-3.5 2.5 1 2.5M10 12.5 7.5 11M12 13l3 1.5M7 17h10",
  lying_immobile: "M8 13.5a1.3 1.3 0 1 0 0 .01M10 14h7M10 14l-.5 2M17 14v2M6.5 16.5h11",
};

export function Sign({ type, size = 26 }: { type: EventType; size?: number }) {
  const family = FAMILY[type];
  return (
    <svg className={`sign sign-${family}`} width={size} height={size} viewBox="0 0 24 24" role="img" aria-label={EVENT_LABEL[type]}>
      {family === "warning" && <path d="M12 2.5 22 20.5H2z" fill="#f4c430" stroke="#15191d" strokeWidth="1.4" strokeLinejoin="round" />}
      {family === "mandatory" && <circle cx="12" cy="12" r="10.5" fill="#2f6fb8" />}
      {family === "prohibition" && <circle cx="12" cy="12" r="9.6" fill="#f4f4f0" stroke="#e5483b" strokeWidth="2.2" />}
      <path d={GLYPH[type]} transform={family === "warning" ? "translate(0 1.6) scale(.86) translate(1.95 1.2)" : undefined}
        fill="none" stroke={family === "mandatory" ? "#fff" : "#15191d"} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      {family === "prohibition" && <path d="M5.3 5.3l13.4 13.4" stroke="#e5483b" strokeWidth="2.2" />}
    </svg>
  );
}
