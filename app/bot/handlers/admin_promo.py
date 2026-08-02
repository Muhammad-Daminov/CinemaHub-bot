"""
Admin promo code generation.

Usage: /createpromo <type> <value> [max_uses] [valid_days] [code]
  type:       balance | premium | percent
  value:      amount (balance), days (premium), or percent (percent)
  max_uses:   integer, or 0/omitted for unlimited
  valid_days: integer, or 0/omitted for no expiry
  code:       custom code, or omitted to auto-generate

Example: /createpromo balance 20000 100 30
  -> 100 uses, 30-day validity, 20000 credited on redemption.
"""
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin import is_admin
from app.core.codegen import generate_code
from app.db.models.promo import PromoDiscountType
from app.db.models.user import User
from app.services.promo import promo_service

router = Router(name="admin_promo")

_TYPE_MAP = {
    "balance": PromoDiscountType.FIXED_AMOUNT_BALANCE,
    "premium": PromoDiscountType.PREMIUM_DAYS,
    "percent": PromoDiscountType.PERCENTAGE_DISCOUNT,
}

USAGE_TEXT = (
    "Usage: /createpromo <balance|premium|percent> <value> [max_uses] [valid_days] [code]\n"
    "Example: /createpromo balance 20000 100 30"
)


@router.message(Command("createpromo"))
async def handle_create_promo(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return  # silently ignore — don't reveal this command's existence to non-admins

    args = (command.args or "").split()
    if len(args) < 2:
        await message.answer(USAGE_TEXT)
        return

    discount_type = _TYPE_MAP.get(args[0].lower())
    if discount_type is None:
        await message.answer(f"⚠️ Noto'g'ri tur. Ruxsat etilgan: {', '.join(_TYPE_MAP)}\n\n{USAGE_TEXT}")
        return

    try:
        value = float(args[1])
        max_uses = int(args[2]) if len(args) > 2 and args[2] != "0" else None
        valid_days = int(args[3]) if len(args) > 3 and args[3] != "0" else None
    except ValueError:
        await message.answer(f"⚠️ Raqamli qiymatlarda xatolik.\n\n{USAGE_TEXT}")
        return

    code = args[4].upper() if len(args) > 4 else generate_code(10)
    valid_until = datetime.utcnow() + timedelta(days=valid_days) if valid_days else None

    creator_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    creator = creator_result.scalar_one_or_none()

    promo = await promo_service.create_promo_code(
        session,
        discount_type=discount_type,
        value=value,
        code=code,
        max_uses=max_uses,
        valid_until=valid_until,
        created_by_id=creator.id if creator else None,
    )

    await message.answer(
        "✅ Promo-kod yaratildi:\n"
        f"Code: <code>{promo.code}</code>\n"
        f"Type: {promo.discount_type.value}\n"
        f"Value: {promo.value}\n"
        f"Max uses: {promo.max_uses or 'unlimited'}\n"
        f"Valid until: {promo.valid_until or 'no expiry'}"
    )
