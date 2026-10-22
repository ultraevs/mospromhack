import { Link } from "react-router-dom";
import { formatTime, SeverityBadge } from "../components/EventBits";
import { useEvents } from "../hooks/useEvents";
import { EVENT_LABEL } from "../types";

export function JournalPage() {
  const { events, error } = useEvents(undefined, 100);

  return (
    <div className="p-4">
      <h1 className="mb-3 text-base font-semibold">Журнал нарушений</h1>
      {error && <div className="mb-3 text-high">▲ {error}</div>}
      <table className="w-full border-collapse text-left">
        <thead className="text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-2 py-2 font-normal">Время</th>
            <th className="px-2 py-2 font-normal">Нарушение</th>
            <th className="px-2 py-2 font-normal">Важность</th>
            <th className="px-2 py-2 font-normal">Камера</th>
            <th className="px-2 py-2 font-normal">Зона</th>
            <th className="px-2 py-2 font-normal">Состояние</th>
            <th className="px-2 py-2 font-normal">Уверенность</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="border-b border-line hover:bg-panel">
              <td className="px-2 py-2 whitespace-nowrap">{formatTime(e.started_at)}</td>
              <td className="px-2 py-2">{EVENT_LABEL[e.type]}</td>
              <td className="px-2 py-2"><SeverityBadge severity={e.severity} /></td>
              <td className="px-2 py-2"><Link className="hover:underline" to={`/cameras/${e.camera_id}`}>{e.camera_name}</Link></td>
              <td className="px-2 py-2 text-muted">{e.zone_name ?? "—"}</td>
              <td className="px-2 py-2">{e.status === "open" ? "идёт" : "закрыто"}</td>
              <td className="px-2 py-2 text-muted">{e.confidence.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {events.length === 0 && <div className="p-3 text-muted">Событий пока нет</div>}
    </div>
  );
}
