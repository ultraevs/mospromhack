from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import SettingRow
from backend.app.services.bus import EventBus
from backend.app.services.hub import Hub
from backend.app.services.sources import SourceManager
from shared.config import AppConfig, Settings

WORKER_TIMEOUT_S = 6.0


@dataclass
class AppState:
    settings: Settings
    config: AppConfig
    session_factory: sessionmaker[Session]
    hub: Hub
    sources: SourceManager
    bus: EventBus = field(default_factory=EventBus)
    webhooks: Any = None
    notifier: Any = None
    telegram_state: dict = field(default_factory=lambda: {"enabled": False, "running": False,
                                                          "username": None, "error": None})
    worker_stats: dict[str, Any] = field(default_factory=dict)
    worker_seen: float = 0.0  # monotonic последнего /internal/stats
    worker_session: str | None = None

    def worker_alive(self) -> bool:
        return bool(self.worker_seen) and time.monotonic() - self.worker_seen < WORKER_TIMEOUT_S

    def camera_online(self, camera_id: int) -> bool:
        if not self.worker_alive():
            return False
        cam = (self.worker_stats.get("cameras") or {}).get(str(camera_id)) or {}
        return bool(cam.get("online"))


def get_state(request: Request) -> AppState:
    return request.app.state.app


def get_state_ws(ws: WebSocket) -> AppState:
    return ws.app.state.app


def get_db(state: AppState = Depends(get_state)):
    with state.session_factory() as db:
        yield db


def require_internal(x_internal_token: str | None = Header(default=None), state: AppState = Depends(get_state)):
    if x_internal_token != state.settings.internal_token:
        raise HTTPException(status_code=401, detail="неверный X-Internal-Token")


CONFIG_VERSION_KEY = "config_version"


def get_config_version(db: Session) -> int:
    row = db.get(SettingRow, CONFIG_VERSION_KEY)
    return int(row.value) if row else 1


# дергать при любом изменении камер/зон/правил, воркер сверяет версию
def bump_config_version(db: Session) -> int:
    row = db.get(SettingRow, CONFIG_VERSION_KEY)
    if row is None:
        row = SettingRow(key=CONFIG_VERSION_KEY, value=1)
        db.add(row)
    row.value = int(row.value) + 1
    db.flush()
    return row.value


def get_setting(db: Session, key: str, default=None):
    row = db.execute(select(SettingRow).where(SettingRow.key == key)).scalar_one_or_none()
    return row.value if row else default


def set_setting(db: Session, key: str, value) -> None:
    row = db.get(SettingRow, key)
    if row is None:
        db.add(SettingRow(key=key, value=value))
    else:
        row.value = value


def media_root(state: AppState):
    return state.settings.data_dir / "media"
