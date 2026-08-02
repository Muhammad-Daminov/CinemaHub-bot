"""
One-off migration: legacy `users` table -> chp_users (+ chp_subscriptions).

Field mapping:
    users.user_id       -> User.telegram_id
    users.balance       -> User.balance
    users.created_at    -> User.created_at (preserve join date)
    users.premium_until -> a Subscription row, only if still in the future
    users.account_number -> ignored (legacy payment identifier, unused here)
    users.ai_requests_today / last_ai_request -> ignored: AI quota now lives
        in Redis with a self-expiring daily key, so yesterday's counter is
        meaningless and must not be carried over.

Username and full_name stay NULL — the legacy table never stored them.
They fill in the next time each user touches the bot.

Read-only against `users`. Idempotent: a telegram_id already in chp_users
is skipped, so re-running adds nothing.

Run:  python -m scripts.migrate_legacy_users
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.core.codegen import generate_code
from app.db.models.user import Subscription, SubscriptionPlan, User
from app.db.session import db_session_ctx, engine


async def fetch_legacy_users(session) -> list[dict]:
    result = await session.execute(
        text("SELECT user_id, created_at, premium_until, balance FROM users ORDER BY id")
    )
    return [dict(row._mapping) for row in result]


async def existing_telegram_ids(session) -> set[int]:
    result = await session.execute(select(User.telegram_id))
    return {row[0] for row in result}


async def existing_referral_codes(session) -> set[str]:
    result = await session.execute(select(User.referral_code))
    return {row[0] for row in result}


def as_aware(value: datetime | None) -> datetime | None:
    """Legacy timestamps are naive; compare them in UTC rather than crashing."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def migrate() -> None:
    stats = defaultdict(int)
    now = datetime.now(timezone.utc)

    async with db_session_ctx() as session:
        rows = await fetch_legacy_users(session)
        seen_ids = await existing_telegram_ids(session)
        used_codes = await existing_referral_codes(session)

        for row in rows:
            telegram_id = row["user_id"]
            if telegram_id is None:
                stats["skipped_no_id"] += 1
                continue
            if telegram_id in seen_ids:
                stats["skipped_existing"] += 1
                continue

            # Referral codes are unique-constrained; retry until a free one.
            code = generate_code()
            while code in used_codes:
                code = generate_code()
            used_codes.add(code)

            user = User(
                telegram_id=telegram_id,
                referral_code=code,
                balance=row.get("balance") or 0,
            )
            created_at = as_aware(row.get("created_at"))
            if created_at:
                user.created_at = created_at

            session.add(user)
            await session.flush()
            seen_ids.add(telegram_id)
            stats["users_migrated"] += 1

            premium_until = as_aware(row.get("premium_until"))
            if premium_until and premium_until > now:
                session.add(
                    Subscription(
                        user_id=user.id,
                        plan=SubscriptionPlan.PREMIUM,
                        expires_at=premium_until,
                    )
                )
                stats["premium_preserved"] += 1
            elif premium_until:
                stats["premium_expired_skipped"] += 1

            if (row.get("balance") or 0) > 0:
                stats["with_balance"] += 1

    await engine.dispose()

    print("\n--- Legacy user migration ---")
    for key in sorted(stats):
        print(f"{key}: {stats[key]}")


if __name__ == "__main__":
    asyncio.run(migrate())
