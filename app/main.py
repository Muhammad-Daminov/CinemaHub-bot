"""
Application entrypoint.

Runs Aiogram in WEBHOOK mode behind FastAPI, rather than polling —
Render's web service model expects one process bound to $PORT serving
HTTP, and webhook mode lets the same FastAPI app both serve the Mini
App's API and receive Telegram updates.
"""
from contextlib import asynccontextmanager
from pathlib import Path

import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Update
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import admin as admin_api
from app.api import billing as billing_api
from app.api import i18n as i18n_api
from app.api import auth as auth_api
from app.api import movies as movies_api
from app.bot.handlers import base as base_handlers
from app.bot.handlers import catalog as catalog_handlers
from app.bot.handlers import streaming as streaming_handlers
from app.bot.handlers import payment as payment_handlers
from app.bot.handlers import admin_payment as admin_payment_handlers
from app.bot.handlers import promo as promo_handlers
from app.bot.handlers import admin_promo as admin_promo_handlers
from app.bot.handlers import admin_upload as admin_upload_handlers
from app.bot.handlers import ai as ai_handlers
from app.bot.instance import bot
from app.bot.middlewares.access import AccessMiddleware
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.i18n import I18nMiddleware
from app.bot.middlewares.throttling import ThrottlingMiddleware
from app.core.config import settings
from app.db.session import check_db_connection, db_session_ctx
from app.services.ai import ai_service
from app.services.permissions import ensure_super_admin
from app.services.tmdb import tmdb_service

dispatcher = Dispatcher(
    storage=RedisStorage.from_url(settings.REDIS_URL, state_ttl=3600, data_ttl=3600)
)

# Order matters: throttling first (cheap, drops spam before it touches the DB),
# then the DB session wrapper, then i18n (which reads the user's language
# through that session), then access control (which refuses banned users and
# non-members, and needs both the session and that translator to say why),
# then routers.
dispatcher.update.middleware(ThrottlingMiddleware())
dispatcher.update.middleware(DbSessionMiddleware())
dispatcher.update.middleware(I18nMiddleware())
dispatcher.update.middleware(AccessMiddleware())
dispatcher.include_router(base_handlers.router)
dispatcher.include_router(catalog_handlers.router)
dispatcher.include_router(streaming_handlers.router)
dispatcher.include_router(payment_handlers.router)
dispatcher.include_router(admin_payment_handlers.router)
dispatcher.include_router(promo_handlers.router)
dispatcher.include_router(admin_promo_handlers.router)
dispatcher.include_router(admin_upload_handlers.router)
dispatcher.include_router(ai_handlers.router)

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook/telegram"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: verify DB, promote the configured Super Admin, register the webhook. Shutdown: tear down cleanly."""
    await check_db_connection()

    # Reconciles the Super Admin against SUPER_ADMIN_TELEGRAM_ID on every
    # boot, which is what makes the setting authoritative: changing it and
    # redeploying transfers the role, demoting the previous holder. Failure
    # is logged rather than fatal — a platform that will not start because
    # one row could not be updated is worse than one running with the
    # previous owner still in place.
    try:
        async with db_session_ctx() as session:
            await ensure_super_admin(session)
    except Exception:
        logger.exception("Could not reconcile the super admin on startup")

    if settings.WEBHOOK_BASE_URL:
        await bot.set_webhook(
            url=f"{settings.WEBHOOK_BASE_URL}{WEBHOOK_PATH}",
            secret_token=settings.WEBHOOK_SECRET,
            # Keep whatever Telegram queued while we were down. A redeploy
            # takes long enough that dropping the queue silently discards
            # every button a user pressed during it.
            drop_pending_updates=False,
        )

    yield

    # Deliberately NOT deleting the webhook here. It is global bot state, not
    # this process's to release: a redeploy would leave the bot unreachable
    # until the new instance boots, and a second environment shutting down
    # last would wipe production's registration entirely. The webhook is only
    # ever set — on startup, by whichever instance owns WEBHOOK_BASE_URL.
    await bot.session.close()
    await tmdb_service.close()
    await ai_service.close()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://web.telegram.org"] if settings.is_production else ["*"],
    allow_credentials=False,
    # PATCH is needed for /api/auth/me (language switch); without it the
    # browser preflight fails in production and the setting silently won't save.
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

app.include_router(auth_api.router, prefix="/api/auth", tags=["auth"])
app.include_router(i18n_api.router, prefix="/api/i18n", tags=["i18n"])
app.include_router(movies_api.router, prefix="/api/movies", tags=["movies"])
app.include_router(billing_api.router, prefix="/api/billing", tags=["billing"])
app.include_router(admin_api.router, prefix="/api/admin", tags=["admin"])

# The Mini App is a separately-built static bundle (webapp/, `npm run build` ->
# webapp/dist). Mounting it here means one Render service serves both the API
# and the frontend — no second service/host to manage. Guarded by exists() so
# local API-only development doesn't require the frontend to be built first.
WEBAPP_DIST_DIR = Path(__file__).resolve().parent.parent / "webapp" / "dist"
if WEBAPP_DIST_DIR.exists():
    app.mount("/miniapp", StaticFiles(directory=str(WEBAPP_DIST_DIR), html=True), name="miniapp")


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check() -> dict[str, str]:
    """Render's health check target. HEAD too — UptimeRobot probes with it."""
    await check_db_connection()
    return {"status": "ok"}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> Response:
    """Receives Telegram updates and hands them to the Aiogram dispatcher."""
    if settings.WEBHOOK_SECRET:
        token_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if token_header != settings.WEBHOOK_SECRET:
            return Response(status_code=401)

    update_data = await request.json()
    update = Update.model_validate(update_data)
    await dispatcher.feed_update(bot=bot, update=update)
    return Response(status_code=200)
