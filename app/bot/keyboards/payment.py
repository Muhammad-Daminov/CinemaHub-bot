"""Keyboards for the manual receipt payment flow (user side and admin side)."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.i18n import t
from app.db.models.payment import AdminCard
from app.db.models.user import UILanguage

TOPUP_AMOUNT_PREFIX = "topup_amt:"
SELECT_CARD_PREFIX = "select_card:"
PAY_APPROVE_PREFIX = "pay_approve:"
PAY_REJECT_PREFIX = "pay_reject:"


def get_topup_amount_keyboard(amounts: list[int]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{amount:,}", callback_data=f"{TOPUP_AMOUNT_PREFIX}{amount}")]
        for amount in amounts
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_card_selection_keyboard(cards: list[AdminCard], lang: UILanguage) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("payment.card_button", lang, bank=c.bank_name or "", number=c.card_number),
                callback_data=f"{SELECT_CARD_PREFIX}{c.id}",
            )
        ]
        for c in cards
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_review_keyboard(receipt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"{PAY_APPROVE_PREFIX}{receipt_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"{PAY_REJECT_PREFIX}{receipt_id}"),
            ]
        ]
    )
