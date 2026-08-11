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
from app.bot.keyboards.catalog import SUBSCRIBE_CALLBACK
from app.bot.keyboards.main_menu import MENU_PREMIUM, menu_texts
from app.bot.keyboards.payment import (
    SELECT_CARD_PREFIX,
    TOPUP_AMOUNT_PREFIX,
    get_card_selection_keyboard,
    get_topup_amount_keyboard,
)
from app.core.config import settings
from app.db.models.payment import AdminCard, PaymentPurpose, PaymentReceipt
from app.services.payment_submission import DuplicateReceiptError, guard_against_duplicate
from app.services.subscription_plans import default_paid_plan
from app.services.subscriptions import is_user_premium
from app.services.users import get_user_id
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


async def start_subscription_purchase(
    target: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    lang: UILanguage,
    _,
) -> None:
    """
    Begins a subscription purchase — the bot's one way to sell one.

    Extracted so the Premium menu button and the subscribe button on a
    locked title card run *identical* code. The alternative was a second
    handler that also reads a plan and starts a payment, which is how a
    project ends up with two prices for the same subscription.

    Price and identity come from the plan table; nothing is passed in by
    the caller, so neither entry point can name an amount.
    """
    message = target.message if isinstance(target, CallbackQuery) else target
    plan = await default_paid_plan(session)
    if plan is None:
        await message.answer(_("payment.no_plans"))
        return

    await _start_payment(
        target, state, session,
        amount=float(plan.price),
        purpose=PaymentPurpose.SUBSCRIPTION,
        subscription_plan=SubscriptionPlan.PREMIUM,
        lang=lang,
        _=_,
        plan_id=plan.id,
    )


@router.message(F.text.in_(menu_texts(MENU_PREMIUM)))
async def handle_premium_start(
    message: Message, state: FSMContext, session: AsyncSession, lang: UILanguage, _
) -> None:
    await start_subscription_purchase(message, state, session, lang, _)


@router.callback_query(F.data == SUBSCRIBE_CALLBACK)
async def handle_subscribe_from_card(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, lang: UILanguage, _
) -> None:
    """
    The subscribe button on a locked title card.

    Deliberately thin: it answers the callback and hands straight to the
    shared starter. Nothing about *which* film was on screen is carried
    over, because a subscription is not sold per title — carrying an id
    here would be the first step towards a second purchase concept.

    An active subscriber is told so rather than being walked into paying
    twice; they reach this only from a stale card, since a card built for
    them is not locked.
    """
    viewer_id = await get_user_id(session, callback.from_user.id)
    if viewer_id is not None and await is_user_premium(session, viewer_id):
        await callback.answer(_("payment.already_subscribed"), show_alert=True)
        return

    await callback.answer()
    await start_subscription_purchase(callback, state, session, lang, _)


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

    # The Mini App and this handler are two doors into one room. The
    # duplicate decision lives in a service used by both, so a photo sent
    # twice — or sent here after the same top-up was already submitted in
    # the app — cannot become two PENDING receipts for one payment. The
    # FSM state is cleared on success, which stops the ordinary repeat;
    # this covers the concurrent and cross-surface cases it cannot.
    try:
        await guard_against_duplicate(
            session,
            user_id=user.id,
            purpose=PaymentPurpose(data["purpose"]),
            card_id=data["card_id"],
            amount=data["amount"],
        )
    except DuplicateReceiptError:
        await state.clear()
        await message.answer(_("payment.duplicate_pending"))
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
