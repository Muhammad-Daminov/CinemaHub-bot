"""
Telegram Mini App auth bridge.

Verifies the `initData` string Telegram signs and hands to every Mini
App on load, per Telegram's documented algorithm:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

  secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
  expected_hash = HMAC_SHA256(key=secret_key, msg=data_check_string)

data_check_string is every field except `hash`, "key=value" sorted by
key and newline-joined. This is the ONLY trustworthy way to identify
a Mini App user server-side — anything else in the request (a raw
telegram_id in a query param, say) is trivially spoofable.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin import is_admin
from app.core.config import settings
from app.db.models.user import UILanguage, User
from app.db.session import get_db_session
from app.services.subscriptions import is_user_premium
from app.services.users import get_or_create_user

router = APIRouter()

INIT_DATA_MAX_AGE_SECONDS = 86400  # 24h — matches Telegram's own recommendation


def verify_init_data(init_data: str, bot_token: str) -> dict[str, str]:
    """Returns the parsed, verified fields. Raises ValueError on any failure."""
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("initData missing hash")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("initData signature mismatch")

    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise ValueError("initData expired")

    return parsed


async def get_current_user(
    x_telegram_init_data: str = Header(..., alias="X-Telegram-Init-Data"),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """FastAPI dependency: verifies initData and get-or-creates the corresponding User."""
    try:
        fields = verify_init_data(x_telegram_init_data, settings.BOT_TOKEN)
        tg_user = json.loads(fields["user"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data") from exc

    full_name = " ".join(filter(None, [tg_user.get("first_name"), tg_user.get("last_name")])) or None
    return await get_or_create_user(session, tg_user["id"], tg_user.get("username"), full_name)


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Layers an admin_ids_list check on top of get_current_user — for every /api/admin/* route."""
    if not is_admin(user.telegram_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class UserProfileOut(BaseModel):
    telegram_id: int
    username: str | None
    full_name: str | None
    balance: float
    referral_code: str
    is_premium: bool
    language: UILanguage
    # False means the user has never picked one and is still on the UZ
    # default — the Mini App shows its first-open picker on that.
    language_selected: bool


class LanguageIn(BaseModel):
    language: UILanguage


def _profile(user: User, premium: bool) -> UserProfileOut:
    return UserProfileOut(
        telegram_id=user.telegram_id,
        username=user.username,
        full_name=user.full_name,
        balance=float(user.balance),
        referral_code=user.referral_code,
        is_premium=premium,
        language=user.language,
        language_selected=user.language_selected,
    )


@router.get("/me", response_model=UserProfileOut)
async def get_my_profile(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)
) -> UserProfileOut:
    premium = await is_user_premium(session, user.id)
    return _profile(user, premium)


@router.patch("/me", response_model=UserProfileOut)
async def update_my_profile(
    payload: LanguageIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserProfileOut:
    """
    Sets the UI language. This is the same column the bot reads, so a
    change here also changes the bot's replies — which is the point.
    """
    user.language = payload.language
    user.language_selected = True
    await session.flush()

    premium = await is_user_premium(session, user.id)
    return _profile(user, premium)
