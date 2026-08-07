"""
User-facing payment FSM.

Two entry points funnel into the same flow: the "⭐ Premium" menu button
(fixed amount/purpose) and /topup (user-picked amount). Both end at the
same screenshot step, which creates one PENDING PaymentReceipt and hands
off to app.bot.handlers.admin_payment for the review notification.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.admin_payment import notify_admins_of_new_receipt
from app.bot.keyboards.main_menu import MENU_PREMIUM, menu_texts
from app.bot.keyboards.payment import (
    SELECT_CARD_PREFIX,
    TOPUP_AMOUNT_PREFIX,
    get_card_selection_keyboard,
    get_topup_amount_keyboard,
)
from app.core.config import settings
from app.db.models.payment import AdminCard, PaymentPurpose, PaymentReceipt
from app.services.subscription_plans import default_paid_plan
from app.db.models.user import SubscriptionPlan, UILanguage, User

router = Router(name="payment")


class PaymentStates(StatesGroup):
    choosing_card = State()
    awaiting_screenshot = State()


async def _start_payment(
    target: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    amount: float,
    purpose: PaymentPurpose,
    subscription_plan: SubscriptionPlan | None,
    lang: UILanguage,
    _,
    plan_id: int | None = None,
) -> None:
    """Shared step after amount/purpose is known: pick a card, then await the screenshot."""
    message = target.message if isinstance(target, CallbackQuery) else target
    cards_result = await session.execute(select(AdminCard).where(AdminCard.is_active.is_(True)))
    cards = list(cards_result.scalars())

    if not cards:
        await message.answer(_("payment.no_cards"))
        return

    await state.update_data(
        amount=amount,
        purpose=purpose.value,
        subscription_plan=subscription_plan.value if subscription_plan else None,
        plan_id=plan_id,
    )

    if len(cards) == 1:
        await state.update_data(card_id=cards[0].id)
        await state.set_state(PaymentStates.awaiting_screenshot)
        await message.answer(_card_prompt_text(cards[0], _))
        return

    await state.set_state(PaymentStates.choosing_card)
    await message.answer(
        _("payment.choose_card"), reply_markup=get_card_selection_keyboard(cards, lang)
    )


def _card_prompt_text(card: AdminCard, _) -> str:
    return _(
        "payment.card_prompt",
        bank=card.bank_name or "",
        number=card.card_number,
        holder=card.holder_name,
    )


@router.message(F.text.in_(menu_texts(MENU_PREMIUM)))
async def handle_premium_start(
    message: Message, state: FSMContext, session: AsyncSession, lang: UILanguage, _
) -> None:
    # Price and identity come from the plan table. PREMIUM_PRICE is only a
    # fallback for a database with no active paid plan, which should not
    # happen but must not make the button silently do nothing.
    plan = await default_paid_plan(session)
    if plan is None:
        await message.answer(_("payment.no_plans"))
        return

    await _start_payment(
        message, state, session,
        amount=float(plan.price),
        purpose=PaymentPurpose.SUBSCRIPTION,
        subscription_plan=SubscriptionPlan.PREMIUM,
        lang=lang,
        _=_,
        plan_id=plan.id,
    )


@router.message(Command("topup"))
async def handle_topup_start(message: Message, _) -> None:
    await message.answer(
        _("payment.topup_prompt"),
        reply_markup=get_topup_amount_keyboard(settings.topup_preset_amounts_list),
    )


@router.callback_query(F.data.startswith(TOPUP_AMOUNT_PREFIX))
async def handle_topup_amount_chosen(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, lang: UILanguage, _
) -> None:
    amount = int(callback.data.removeprefix(TOPUP_AMOUNT_PREFIX))
    await _start_payment(
        callback, state, session,
        amount=amount,
        purpose=PaymentPurpose.TOPUP,
        subscription_plan=None,
        lang=lang,
        _=_,
    )
    await callback.answer()


@router.callback_query(PaymentStates.choosing_card, F.data.startswith(SELECT_CARD_PREFIX))
async def handle_card_selected(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, _
) -> None:
    card_id = int(callback.data.removeprefix(SELECT_CARD_PREFIX))
    card = await session.get(AdminCard, card_id)
    if card is None:
        await callback.answer(_("payment.card_not_found"), show_alert=True)
        return

    await state.update_data(card_id=card.id)
    await state.set_state(PaymentStates.awaiting_screenshot)
    await callback.message.answer(_card_prompt_text(card, _))
    await callback.answer()


@router.message(PaymentStates.awaiting_screenshot, F.photo)
async def handle_receipt_screenshot(
    message: Message, state: FSMContext, session: AsyncSession, _
) -> None:
    data = await state.get_data()
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        await message.answer(_("common.need_start"))
        await state.clear()
        return

    receipt = PaymentReceipt(
        user_id=user.id,
        admin_card_id=data["card_id"],
        purpose=PaymentPurpose(data["purpose"]),
        subscription_plan=SubscriptionPlan(data["subscription_plan"]) if data.get("subscription_plan") else None,
        plan_id=data.get("plan_id"),
        amount=data["amount"],
        receipt_photo_file_id=message.photo[-1].file_id,
    )
    session.add(receipt)
    await session.flush()
    await state.clear()

    await message.answer(_("payment.received"))
    await notify_admins_of_new_receipt(message.bot, session, user, receipt)
