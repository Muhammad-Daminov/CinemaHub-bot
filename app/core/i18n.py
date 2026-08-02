"""
Translation lookup for bot-facing text.

Catalogs are flat {dotted.key: string} JSON, loaded once at import —
they're a few KB and never change at runtime, so there's no reason to
hit the filesystem per message.

Uzbek is the reference locale: it's the one the strings were originally
written in, and it's the default for every user. A key missing from
ru/en therefore falls back to uz rather than showing nothing.

t() never raises. A handler that can't render a label should still send
*something* — a missing translation is a content bug to fix in the JSON,
not a reason to 500 a user's /start.
"""
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import UILanguage, User

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
FALLBACK_LANGUAGE = UILanguage.UZ


def _load(language: UILanguage) -> dict[str, str]:
    path = LOCALES_DIR / f"{language.value}.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


CATALOGS: dict[UILanguage, dict[str, str]] = {lang: _load(lang) for lang in UILanguage}


def t(key: str, lang: UILanguage = FALLBACK_LANGUAGE, **kwargs) -> str:
    """
    Translated string for `key`, with {placeholders} filled from kwargs.

    Falls back to Uzbek, then to the key itself. A formatting failure
    (missing/extra kwarg) returns the raw template rather than blowing up
    mid-handler.
    """
    template = CATALOGS.get(lang, {}).get(key)

    if template is None and lang is not FALLBACK_LANGUAGE:
        template = CATALOGS[FALLBACK_LANGUAGE].get(key)
        if template is not None:
            logger.warning("Missing %s translation for key %r — using %s", lang.value, key, FALLBACK_LANGUAGE.value)

    if template is None:
        logger.warning("Missing translation key %r in every locale", key)
        return key

    if not kwargs:
        return template

    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        logger.warning("Could not format key %r for %s with %s", key, lang.value, sorted(kwargs))
        return template


async def t_for_user(session: AsyncSession, user_id: int, key: str, **kwargs) -> str:
    """
    Translate for a specific user id, looking their language up directly.

    For service-layer code that sends a message outside a handler's
    middleware context (payment approval notifications, cron jobs) and so
    has no injected `_`. Selects only the language column, and falls back
    to Uzbek if the user has since been deleted.
    """
    result = await session.execute(select(User.language).where(User.id == user_id))
    lang = result.scalar_one_or_none() or FALLBACK_LANGUAGE
    return t(key, lang, **kwargs)


def all_translations(key: str) -> set[str]:
    """
    Every locale's rendering of `key`.

    Used to match reply-keyboard button presses: the label arrives as
    plain text, and a user who just switched language may still have the
    previous keyboard on screen, so every variant has to be accepted.
    """
    return {t(key, lang) for lang in UILanguage}
