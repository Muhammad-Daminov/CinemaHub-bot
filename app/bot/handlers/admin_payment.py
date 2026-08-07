"""
Admin review flow for submitted payment receipts (bot side).

The actual DB transaction (credit balance, activate subscription,
notify the user) lives in app.services.payment_review — shared with
the admin REST API — so this file only handles the Telegram-specific
parts: the notification with Approve/Reject buttons, the rejection
FSM, and editing the admin's message after a decision.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.payment import PAY_APPROVE_PREFIX, PAY_REJECT_PREFIX, get_admin_review_keyboard
from app.bot.permissions import actor_with_permission, admins_with_permission
from app.core.permissions import Permission
from app.db.models.payment import PaymentReceipt
from app.db.models.user import User
from app.services.payment_review import (
    ReceiptNotFoundError,
    ReceiptReviewError,
    approve_receipt,
    reject_receipt,
)

router = Router(name="admin_payment")
logger = logging.getLogger(__name__)


class AdminPaymentStates(StatesGroup):
    awaiting_rejection_reason = State()


async def notify_admins_of_new_receipt(
    bot: Bot, session: AsyncSession, user: User, receipt: PaymentReceipt
) -> None:
    """
    Sends the screenshot and Approve/Reject buttons to administrators who
    can actually act on it.

    Addressed by permission rather than to every configured admin id: the
    buttons only work for someone holding MANAGE_PAYMENTS, so notifying
    anyone else hands them a control that will refuse them — and shows a
    user's payment screenshot to administrators with no business seeing it.
    """
    caption = (
        f"🧾 <b>New payment receipt</b>\n"
        f"User: {user.full_name or user.username or user.telegram_id} (<code>{user.telegram_id}</code>)\n"
        f"Purpose: {receipt.purpose.value}"
        + (f" ({receipt.subscription_plan.value})" if receipt.subscription_plan else "")
        + f"\nAmount: {receipt.amount:,}"
    )
    reviewers = await admins_with_permission(session, Permission.MANAGE_PAYMENTS)
    if not reviewers:
        logger.error("No administrator holds MANAGE_PAYMENTS — receipt %s unreviewed", receipt.id)

    for reviewer in reviewers:
        try:
            await bot.send_photo(
                chat_id=reviewer.telegram_id,
                photo=receipt.receipt_photo_file_id,
                caption=caption,
                reply_markup=get_admin_review_keyboard(receipt.id),
            )
        except TelegramForbiddenError:
            logger.warning(
                "Admin %s has not started the bot — cannot notify", reviewer.telegram_id
            )


async def _get_reviewer_id(session: AsyncSession, telegram_id: int) -> int | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    reviewer = result.scalar_one_or_none()
    return reviewer.id if reviewer else None


@router.callback_query(F.data.startswith(PAY_APPROVE_PREFIX))
async def handle_approve(callback: CallbackQuery, session: AsyncSession, _) -> None:
    actor = await actor_with_permission(
        session, callback.from_user.id, Permission.MANAGE_PAYMENTS
    )
    if actor is None:
        await callback.answer(_("admin.no_permission"), show_alert=True)
        return

    receipt_id = int(callback.data.removeprefix(PAY_APPROVE_PREFIX))
    reviewer_id = await _get_reviewer_id(session, callback.from_user.id)
    try:
        await approve_receipt(session, receipt_id, reviewer_id)
    except ReceiptNotFoundError:
        await callback.answer(_("admin.receipt_not_found"), show_alert=True)
        return
    except ReceiptReviewError:
        await callback.answer(_("admin.receipt_already_reviewed"), show_alert=True)
        return

    await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ Approved", reply_markup=None)
    await callback.answer()


@router.callback_query(F.data.startswith(PAY_REJECT_PREFIX))
async def handle_reject_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, _
) -> None:
    actor = await actor_with_permission(
        session, callback.from_user.id, Permission.MANAGE_PAYMENTS
    )
    if actor is None:
        await callback.answer(_("admin.no_permission"), show_alert=True)
        return

    receipt_id = int(callback.data.removeprefix(PAY_REJECT_PREFIX))
    await state.update_data(reject_receipt_id=receipt_id)
    await state.set_state(AdminPaymentStates.awaiting_rejection_reason)
    await callback.message.answer(_("admin.reject_reason_prompt"))
    await callback.answer()


@router.message(AdminPaymentStates.awaiting_rejection_reason)
async def handle_reject_reason(
    message: Message, state: FSMContext, session: AsyncSession, _
) -> None:
    data = await state.get_data()
    receipt_id = data["reject_receipt_id"]
    await state.clear()

    reviewer_id = await _get_reviewer_id(session, message.from_user.id)
    try:
        await reject_receipt(session, receipt_id, reviewer_id, message.text)
    except ReceiptNotFoundError:
        await message.answer(_("admin.receipt_not_found"))
        return
    except ReceiptReviewError:
        await message.answer(_("admin.receipt_already_reviewed"))
        return

    await message.answer(_("admin.rejected_notified"))
