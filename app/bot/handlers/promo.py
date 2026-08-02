"""User-facing promo code redemption: `/promo CODE123` or `/promo` for interactive entry."""
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.services.promo import PromoError, promo_service

router = Router(name="promo")


class PromoStates(StatesGroup):
    awaiting_code = State()


async def _redeem_and_reply(message: Message, session: AsyncSession, code: str, _) -> None:
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        await message.answer(_("common.need_start"))
        return

    try:
        _promo, effect_key, effect_params = await promo_service.redeem(session, user, code)
    except PromoError as exc:
        # exc carries a translation key, not a sentence.
        await message.answer(_("promo.error", error=_(str(exc))))
        return

    await message.answer(_("promo.success", effect=_(effect_key, **effect_params)))


@router.message(Command("promo"))
async def handle_promo_command(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession, _
) -> None:
    if command.args:
        await _redeem_and_reply(message, session, command.args.strip(), _)
        return

    await state.set_state(PromoStates.awaiting_code)
    await message.answer(_("promo.enter_code"))


@router.message(PromoStates.awaiting_code, F.text)
async def handle_promo_code_input(
    message: Message, state: FSMContext, session: AsyncSession, _
) -> None:
    await state.clear()
    await _redeem_and_reply(message, session, message.text.strip(), _)
