"""Shared user provisioning — used by both the bot's /start and the Mini App auth bridge."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.codegen import generate_code
from app.db.models.user import User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    full_name: str | None,
    referral_payload: str | None = None,
) -> User:
    """Fetches the User for `telegram_id`, creating it (with optional referral) on first contact."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    referred_by_id: int | None = None
    if referral_payload and referral_payload.startswith("REF_"):
        ref_result = await session.execute(
            select(User).where(User.referral_code == referral_payload.removeprefix("REF_"))
        )
        referrer = ref_result.scalar_one_or_none()
        if referrer is not None:
            referred_by_id = referrer.id

    user = User(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
        referral_code=generate_code(),
        referred_by_id=referred_by_id,
    )
    session.add(user)
    await session.flush()
    return user
