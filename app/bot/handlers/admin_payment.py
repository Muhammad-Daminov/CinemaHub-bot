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
from app.core.admin import is_admin
from app.core.config import settings
from app.db.models.payment import PaymentReceipt
from app.db.models.user import User
from app.services.payment_review import ReceiptReviewError, approve_receipt, reject_receipt

router = Router(name="admin_payment")
logger = logging.getLogger(__name__)


class AdminPaymentStates(StatesGroup):
    awaiting_rejection_reason = State()


async def notify_admins_of_new_receipt(bot: Bot, user: User, receipt: PaymentReceipt) -> None:
    """Sends the screenshot + user/payment info + Approve/Reject buttons to every configured admin."""
    caption = (
        f"🧾 <b>New payment receipt</b>\n"
        f"User: {user.full_name or user.username or user.telegram_id} (<code>{user.telegram_id}</code>)\n"
        f"Purpose: {receipt.purpose.value}"
        + (f" ({receipt.subscription_plan.value})" if receipt.subscription_plan else "")
        + f"\nAmount: {receipt.amount:,}"
    )
    for admin_id in settings.admin_ids_list:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=receipt.receipt_photo_file_id,
                caption=caption,
                reply_markup=get_admin_review_keyboard(receipt.id),
            )
        except TelegramForbiddenError:
            logger.warning("Admin %s has not started the bot — cannot notify", admin_id)


async def _get_reviewer_id(session: AsyncSession, telegram_id: int) -> int | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    reviewer = result.scalar_one_or_none()
    return reviewer.id if reviewer else None


@router.callback_query(F.data.startswith(PAY_APPROVE_PREFIX))
async def handle_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    receipt_id = int(callback.data.removeprefix(PAY_APPROVE_PREFIX))
    receipt = await session.get(PaymentReceipt, receipt_id)
    if receipt is None:
        await callback.answer("Chek topilmadi.", show_alert=True)
        return

    reviewer_id = await _get_reviewer_id(session, callback.from_user.id)
    try:
        await approve_receipt(session, receipt, reviewer_id)
    except ReceiptReviewError:
        await callback.answer("Bu chek allaqachon ko'rib chiqilgan.", show_alert=True)
        return

    await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ Approved", reply_markup=None)
    await callback.answer()


@router.callback_query(F.data.startswith(PAY_REJECT_PREFIX))
async def handle_reject_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    receipt_id = int(callback.data.removeprefix(PAY_REJECT_PREFIX))
    await state.update_data(reject_receipt_id=receipt_id)
    await state.set_state(AdminPaymentStates.awaiting_rejection_reason)
    await callback.message.answer("Rad etish sababini yozing:")
    await callback.answer()


@router.message(AdminPaymentStates.awaiting_rejection_reason)
async def handle_reject_reason(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    receipt = await session.get(PaymentReceipt, data["reject_receipt_id"])
    await state.clear()

    if receipt is None:
        await message.answer("Chek topilmadi.")
        return

    reviewer_id = await _get_reviewer_id(session, message.from_user.id)
    try:
        await reject_receipt(session, receipt, reviewer_id, message.text)
    except ReceiptReviewError:
        await message.answer("Bu chek allaqachon ko'rib chiqilgan.")
        return

    await message.answer("Rad etildi va foydalanuvchiga xabar yuborildi.")
