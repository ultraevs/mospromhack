import { useEffect, useRef, useState } from "react";
import { applyMessage, beep, loadPrefs, onLive, unlockAudio } from "../lib/alerts";
import { formatTime } from "../lib/format";
import { navigate } from "../lib/router";
import { EVENT_LABEL, type SafetyEvent } from "../types";
import { Sign } from "./Sign";

const openEvent = (id: string) => navigate(`/events?event=${encodeURIComponent(id)}`);

function notify(e: SafetyEvent) {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const n = new Notification(`Критичное: ${EVENT_LABEL[e.type]}`, {
      body: `${e.camera_name} · ${formatTime(e.started_at)}`,
      tag: e.id,
    });
    n.onclick = () => {
      window.focus();
      openEvent(e.id);
      n.close();
    };
  } catch {
    // уведомления не критичны
  }
}

export function CriticalAlerts() {
  const [list, setList] = useState<SafetyEvent[]>([]);
  const [prefs, setPrefs] = useState(loadPrefs);
  const hidden = useRef(new Set<string>());

  useEffect(() => {
    const sync = () => setPrefs(loadPrefs());
    window.addEventListener("alert-prefs", sync);
    window.addEventListener("click", unlockAudio);
    window.addEventListener("keydown", unlockAudio);
    return () => {
      window.removeEventListener("alert-prefs", sync);
      window.removeEventListener("click", unlockAudio);
      window.removeEventListener("keydown", unlockAudio);
    };
  }, []);

  useEffect(() => onLive((m) => {
    if (m.type === "event_created" && m.event.severity === "high" && m.event.review === "none") {
      if (loadPrefs().sound) beep();
      notify(m.event);
    }
    setList((cur) => applyMessage(cur, m, hidden.current));
  }), []);

  const hide = (id: string) => {
    hidden.current.add(id);
    setList((cur) => cur.filter((e) => e.id !== id));
  };

  if (!prefs.banner || !list.length) return null;
  return (
    <div className="crit-stack" role="alert">
      {list.map((e) => (
        <div key={e.id} className="crit-item">
          <Sign type={e.type} size={28} />
          <div className="crit-text">
            <strong>{EVENT_LABEL[e.type]}</strong>
            <span>{e.camera_name}{e.zone_name ? ` · ${e.zone_name}` : ""} · {formatTime(e.started_at)}</span>
          </div>
          <button type="button" className="btn btn-primary" onClick={() => { openEvent(e.id); hide(e.id); }}>Открыть</button>
          <button type="button" className="btn btn-ghost" onClick={() => hide(e.id)}>Скрыть</button>
        </div>
      ))}
    </div>
  );
}
