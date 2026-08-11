"""Shared user provisioning — used by both the bot's /start and the Mini App auth bridge."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.codegen import generate_code
from app.db.models.user import User


async def get_user_id(session: AsyncSession, telegram_id: int) -> int | None:
    """
    Internal chp_users.id for a Telegram id, or None if they never /start-ed.

    Selects the id column only — callers want it as a foreign key for
    favourites or watch history, not the whole row, and loading the ORM
    object just to read `.id` is the shape that keeps reintroducing lazy
    attribute access under async.
    """
    result = await session.execute(select(User.id).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    full_name: str | None,
    referral_payload: str | None = None,
) -> User:
    """
    Fetches the User for `telegram_id`, creating it (with optional referral) on first contact.

    For an existing user this also refreshes their Telegram profile
    fields. They were previously written once at signup and never again,
    so anyone who changed their Telegram username or display name kept
    the old value forever — visible in the admin user list, in the
    welcome message, and in the Mini App's settings screen. Telegram
    sends the current values on every update, so the fix is just to
    notice when they differ.

    Only assigns on an actual change: an unconditional write would dirty
    the row on every single /start and turn a read into a write.
    """
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is not None:
        if username != user.username:
            user.username = username
        if full_name and full_name != user.full_name:
            user.full_name = full_name
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

    # A genuinely new person, so this is the one moment the trial can be
    # offered. Inside the same transaction as the signup: a trial that
    # survived a rolled-back registration would belong to nobody, and a
    # signup that failed because the trial did would be far worse than no
    # trial. Returns None quietly when the offer is off or the person has
    # had a subscription before.
    from app.services.trial import grant_trial_if_eligible

    await grant_trial_if_eligible(session, user)
    return user
