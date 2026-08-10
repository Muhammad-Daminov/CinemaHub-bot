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
from app.api.rate_limit import RateLimitMiddleware
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
from app.bot.commands import register_bot_commands
from app.services.settings_store import maintenance_is_stale
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

    # Scheduled maintenance is a separate process this repository does not
    # own (app/tasks/cron.py, run by a Render Cron Job configured in the
    # dashboard). Whether it runs at all was previously unknowable from
    # here, while the 30-day receipt-image retention promise depended on
    # it. Each run now stamps chp_system_settings, and this shouts when
    # that stamp is missing or stale. Warn-only: a maintenance job that is
    # not running is a serious problem, but not a reason to refuse to
    # serve traffic.
    try:
        async with db_session_ctx() as session:
            stale, last_run = await maintenance_is_stale(session)
        if stale:
            logger.warning(
                "SCHEDULED MAINTENANCE OVERDUE — last run %s. Receipt images are not "
                "being purged and stale receipts are not being expired. Verify the "
                "Render Cron Job for `python -m app.tasks.cron` exists.",
                last_run.isoformat() if last_run else "never",
            )
        else:
            logger.info("Scheduled maintenance last ran at %s", last_run.isoformat())
    except Exception:
        logger.exception("Could not check the scheduled-maintenance heartbeat")

    # Populates Telegram's "/" menu. Without this the bot's only commands —
    # /topup and /promo — were invisible three ways over: absent from the
    # reply keyboard, absent from /help, and absent from the command menu,
    # which is where a Telegram user actually looks. Registered here, in
    # the one startup path, so the menu cannot drift from the handlers.
    #
    # Per-language scopes, because a Russian speaker should not be offered
    # Uzbek command descriptions. Failure is logged, never fatal: a bot
    # that refuses to boot because a cosmetic menu did not update would be
    # a far worse outage than a stale menu.
    try:
        await register_bot_commands()
    except Exception:
        logger.exception("Could not register the Telegram command menu")

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


def docs_urls(production: bool) -> dict[str, str | None]:
    """
    Where the interactive API documentation lives, or nowhere.

    Swagger, ReDoc and the raw schema are a development convenience, not a
    production feature: nothing in the bot or the Mini App reads them, and
    in production they publish the entire admin surface — every route,
    parameter and model name — to anyone who asks. That leaks no data and
    no secret, since every route still requires verified Telegram
    `initData`, but it is free reconnaissance for someone deciding where
    to push.

    Turned off rather than put behind a password. A login on a docs page
    is a second authentication mechanism to build, test and get wrong,
    guarding something production does not need at all; removing the
    routes is the smaller change and cannot be misconfigured open.

    Keyed on the existing `ENVIRONMENT` setting — the same switch that
    already narrows CORS to Telegram's origin — so there is no new
    deployment concept and no hostname hardcoded anywhere.

    `app.openapi()` is unaffected: the schema is still built in-process,
    which is what `tests/test_api_schema.py` checks and what would
    otherwise let a broken response model ship unnoticed.
    """
    if production:
        return {"openapi_url": None, "docs_url": None, "redoc_url": None}
    return {"openapi_url": "/openapi.json", "docs_url": "/docs", "redoc_url": "/redoc"}


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    **docs_urls(settings.is_production),
)

# Registered before CORS so it runs *after* it: Starlette applies
# middleware in reverse order of registration, and a preflight OPTIONS
# must be answered by CORS rather than counted against a rate limit.
app.add_middleware(RateLimitMiddleware)

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
    """
    Render's health check target. HEAD too — UptimeRobot probes with it.

    Reports the running commit so a deploy can be confirmed from outside
    the dashboard. `status` keeps its exact previous shape and value: the
    Render health check and the uptime monitor both read it, and this is
    not the place to change a contract two external systems depend on.

    "unknown" locally, where RENDER_GIT_COMMIT is not injected — a
    developer machine has no commit to report, and inventing one (from
    git, say) would report the checkout rather than what is deployed.
    """
    await check_db_connection()
    commit = settings.RENDER_GIT_COMMIT or "unknown"
    return {
        "status": "ok",
        "commit": commit,
        "version": commit[:7] if commit != "unknown" else "development",
    }


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
