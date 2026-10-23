from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import asyncio

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.app.api import analytics, cameras, events, internal, notifications, public, zones
from backend.app.db.session import make_engine, make_session_factory
from backend.app.services.hub import Hub
from backend.app.services.telegram import AiogramSender, TelegramNotifier, run_bot
from backend.app.services.webhooks import WebhookDispatcher
from backend.app.services.rules import seed_rules
from backend.app.services.sources import SourceManager
from backend.app.state import AppState
from shared.config import ROOT, Settings, get_settings, load_config

log = logging.getLogger("backend")


def create_app(settings: Settings | None = None, run_sources: bool = True, telegram_sender=None,
               webhook_retry_pauses: tuple[float, ...] | None = None) -> FastAPI:
    settings = settings or get_settings()
    config = load_config(settings.config_path)
    engine = make_engine(settings.data_dir / "db.sqlite")
    sources = SourceManager(videos_dir=settings.data_dir / "videos", logs_dir=ROOT / "logs",
                            rtsp_port=settings.mediamtx_rtsp_port, api_port=settings.mediamtx_api_port,
                            active=run_sources, host=settings.mediamtx_host,
                            width=settings.stream_width, maxrate=settings.stream_maxrate)
    media_dir = settings.data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    state = AppState(settings=settings, config=config, session_factory=make_session_factory(engine),
                     hub=Hub(), sources=sources)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with state.session_factory() as db:
            seed_rules(db, config)
            cameras.sync_sources(db, state)
        sources.start()

        state.webhooks = (WebhookDispatcher(state.session_factory, retry_pauses=webhook_retry_pauses)
                          if webhook_retry_pauses is not None else WebhookDispatcher(state.session_factory))
        state.bus.subscribe("webhooks", state.webhooks.handle)
        bot_task = None
        sender = telegram_sender if settings.telegram_enabled else None
        if not settings.telegram_enabled:
            state.telegram_state["reason"] = "switched_off"
        elif sender is None and settings.telegram_bot_token:
            try:
                sender = AiogramSender(settings.telegram_bot_token)
            except Exception as e:  # noqa: BLE001
                state.telegram_state["error"] = f"токен бота не принят: {e}"
                log.error(state.telegram_state["error"])
        if sender is not None:
            state.notifier = TelegramNotifier(state.session_factory, sender, media_dir, config.telegram)
            state.bus.subscribe("telegram", state.notifier.handle)
            state.telegram_state.update(enabled=True, reason=None)
            if isinstance(sender, AiogramSender):
                bot_task = asyncio.create_task(run_bot(sender, state.notifier, state.telegram_state), name="telegram-bot")
        elif not settings.telegram_enabled:
            log.info("телеграм-бот выключен: TELEGRAM_ENABLED=false")
        else:
            log.info("телеграм-бот выключен: TELEGRAM_BOT_TOKEN пуст")
        state.bus.start()

        log.info("backend запущен, порт %s", settings.backend_port)
        yield
        if bot_task:
            bot_task.cancel()
            await asyncio.gather(bot_task, return_exceptions=True)
        await state.bus.stop()
        await state.webhooks.close()
        if state.notifier:
            await state.notifier.close()
        sources.stop()
        engine.dispose()

    app = FastAPI(title="Контроль нарушений по видео", lifespan=lifespan)
    app.state.app = state
    for module in (cameras, zones, events, analytics, notifications, internal, public):
        app.include_router(module.router)
    app.mount("/media", StaticFiles(directory=media_dir), name="media")
    return app


# uvicorn backend.app.main:app --factory
def app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # воркер долбит stats/config каждую секунду, не засоряем лог
    logging.getLogger("uvicorn.access").addFilter(
        lambda r: "/internal/stats" not in r.getMessage() and "/internal/config" not in r.getMessage())
    return create_app()
