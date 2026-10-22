from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api import cameras, events, internal, public
from backend.app.db.session import make_engine, make_session_factory
from backend.app.services.hub import Hub
from backend.app.services.rules import seed_rules
from backend.app.services.sources import SourceManager
from backend.app.state import AppState
from shared.config import ROOT, Settings, get_settings, load_config

log = logging.getLogger("backend")


def create_app(settings: Settings | None = None, run_sources: bool = True) -> FastAPI:
    settings = settings or get_settings()
    config = load_config(settings.config_path)
    engine = make_engine(settings.data_dir / "db.sqlite")
    sources = SourceManager(videos_dir=settings.data_dir / "videos", logs_dir=ROOT / "logs",
                            rtsp_port=settings.mediamtx_rtsp_port, api_port=settings.mediamtx_api_port,
                            active=run_sources)
    state = AppState(settings=settings, config=config, session_factory=make_session_factory(engine),
                     hub=Hub(), sources=sources)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with state.session_factory() as db:
            seed_rules(db, config)
            cameras.sync_sources(db, state)
        sources.start()
        log.info("backend запущен, порт %s", settings.backend_port)
        yield
        sources.stop()
        engine.dispose()

    app = FastAPI(title="Контроль нарушений по видео", lifespan=lifespan)
    app.state.app = state
    for module in (cameras, events, internal, public):
        app.include_router(module.router)
    return app


# uvicorn backend.app.main:app --factory
def app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # воркер долбит stats/config каждую секунду, не засоряем лог
    logging.getLogger("uvicorn.access").addFilter(
        lambda r: "/internal/stats" not in r.getMessage() and "/internal/config" not in r.getMessage())
    return create_app()
