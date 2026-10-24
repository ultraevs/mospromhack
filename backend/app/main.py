from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import asyncio

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from backend.app.api import analytics, auth, cameras, events, internal, notifications, public, uploads, zones
from backend.app.db.session import make_engine, make_session_factory
from backend.app.services.hub import Hub
from backend.app.services.auth import seed_users
from backend.app.services.telegram import AiogramSender, TelegramNotifier, load_enabled, run_bot
from backend.app.services.webhooks import WebhookDispatcher
from backend.app.services.rules import seed_rules
from backend.app.services.sources import SourceManager
from backend.app.state import AppState, get_current_user, get_state
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
                            width=settings.stream_width, maxrate=settings.stream_maxrate, lag_s=settings.sync_lag_s,
                            codec=settings.stream_codec)
    media_dir = settings.data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    state = AppState(settings=settings, config=config, session_factory=make_session_factory(engine),
                     hub=Hub(), sources=sources)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        telegram_enabled = settings.telegram_enabled
        with state.session_factory() as db:
            seed_rules(db, config)
            if settings.auth_enabled:
                seed_users(db, settings)
            telegram_enabled = settings.telegram_enabled and load_enabled(db, True)
            cameras.sync_sources(db, state)
        sources.start()

        state.webhooks = (WebhookDispatcher(state.session_factory, retry_pauses=webhook_retry_pauses)
                          if webhook_retry_pauses is not None else WebhookDispatcher(state.session_factory))
        state.bus.subscribe("webhooks", state.webhooks.handle)
        bot_task = None
        sender = telegram_sender if telegram_enabled else None
        state.telegram_sender = sender
        if not telegram_enabled:
            state.telegram_state["reason"] = "switched_off"
        elif sender is None and settings.telegram_bot_token:
            try:
                sender = AiogramSender(settings.telegram_bot_token)
            except Exception as e:  # noqa: BLE001
                state.telegram_state["reason"] = "invalid_token"
                state.telegram_state["error"] = f"токен бота не принят: {e}"
                log.error(state.telegram_state["error"])
        if sender is not None:
            state.notifier = TelegramNotifier(state.session_factory, sender, media_dir, config.telegram)
            state.telegram_sender = sender
            state.bus.subscribe("telegram", state.notifier.handle)
            state.telegram_state.update(enabled=True, reason=None)
            if isinstance(sender, AiogramSender):
                bot_task = asyncio.create_task(run_bot(sender, state.notifier, state.telegram_state), name="telegram-bot")
                state.telegram_bot_task = bot_task
        elif not telegram_enabled:
            log.info("телеграм-бот выключен: TELEGRAM_ENABLED=false")
        else:
            log.info("телеграм-бот выключен: TELEGRAM_BOT_TOKEN пуст")
        state.bus.start()

        log.info("backend запущен, порт %s", settings.backend_port)
        yield
        active_bot_task = state.telegram_bot_task or bot_task
        if active_bot_task and not active_bot_task.done():
            active_bot_task.cancel()
            await asyncio.gather(active_bot_task, return_exceptions=True)
        state.telegram_bot_task = None
        await state.bus.stop()
        await state.webhooks.close()
        if state.notifier:
            await state.notifier.close()
        sources.stop()
        engine.dispose()

    app = FastAPI(title="Контроль нарушений по видео", lifespan=lifespan)
    app.state.app = state
    for module in (auth, cameras, uploads, zones, events, analytics, notifications, internal, public):
        app.include_router(module.router)
    @app.get("/media/{file_path:path}", include_in_schema=False)
    async def media_file(file_path: str, _user=Depends(get_current_user), state: AppState = Depends(get_state)):
        path = (media_dir / file_path).resolve()
        if media_dir.resolve() not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="файл не найден")
        return FileResponse(path)

    return app


# uvicorn backend.app.main:app --factory
def app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # воркер долбит stats/config каждую секунду, не засоряем лог
    logging.getLogger("uvicorn.access").addFilter(
        lambda r: "/internal/stats" not in r.getMessage() and "/internal/config" not in r.getMessage())
    return create_app()
