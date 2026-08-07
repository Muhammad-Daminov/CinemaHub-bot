"""Keyboards for movie discovery and the watch flow."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.i18n import t
from app.db.models.user import UILanguage

WATCH_CALLBACK_PREFIX = "watch:"
GENRE_CALLBACK_PREFIX = "genre:"


def get_watch_keyboard(episode_id: int, lang: UILanguage) -> InlineKeyboardMarkup:
    """Single Watch button that triggers delivery."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("catalog.btn_watch", lang),
                    callback_data=f"{WATCH_CALLBACK_PREFIX}{episode_id}",
                )
            ]
        ]
    )


def get_genre_filter_keyboard(genres: list[str]) -> InlineKeyboardMarkup:
    """Grid of genre buttons, two per row, for catalog filtering."""
    buttons = [
        InlineKeyboardButton(text=genre, callback_data=f"{GENRE_CALLBACK_PREFIX}{genre}") for genre in genres
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_resend_keyboard(episode_id: int, lang: UILanguage) -> InlineKeyboardMarkup:
    """Lets the user re-request a file — handy if they cleared the chat themselves."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("streaming.btn_resend", lang),
                    callback_data=f"{WATCH_CALLBACK_PREFIX}{episode_id}",
                )
            ]
        ]
    )
