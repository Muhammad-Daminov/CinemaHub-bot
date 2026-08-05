"""
Serves the bot's own translation catalogs to the Mini App.

The point is that there is exactly one set of strings. The Mini App used
to carry its own hardcoded Uzbek, which meant every wording change had to
be made twice and the two drifted apart the moment one was forgotten.
Now both read app/locales/*.json through app.core.i18n.

Deliberately unauthenticated: these are the same files that ship in the
repository, they contain no user data, and the app needs them to render
its very first screen — including the language picker a brand-new user
sees before anything else. Putting them behind initData would mean an
auth hiccup renders a blank UI instead of a degraded one.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.i18n import CATALOGS, FALLBACK_LANGUAGE
from app.db.models.user import UILanguage

router = APIRouter()


class TranslationsOut(BaseModel):
    language: str
    translations: dict[str, str]


@router.get("/{lang}", response_model=TranslationsOut)
async def get_translations(lang: str) -> TranslationsOut:
    """
    Flat {key: template} map for one language.

    Missing keys are filled from Uzbek before sending rather than left to
    the client, so the frontend's fallback only ever has to handle a key
    that exists in no locale at all.
    """
    try:
        language = UILanguage(lang)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown language '{lang}'") from exc

    merged = {**CATALOGS[FALLBACK_LANGUAGE], **CATALOGS[language]}
    return TranslationsOut(language=language.value, translations=merged)
