"""
Main menu keyboards: a persistent ReplyKeyboard plus an inline Mini App
launcher, and the language picker.

Reply-keyboard buttons arrive as plain text, so handlers can't match a
single constant once labels are translated. They match on
`menu_texts(key)` instead — the set of every locale's rendering of that
label. That also covers the stale-keyboard case: a user who just
switched language still has the old keyboard rendered until Telegram
replaces it, and those buttons keep working.
"""
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from app.core.config import settings
from app.core.i18n import all_translations, t
from app.db.models.user import UILanguage

# --- Menu translation keys (handlers match on these, never on a literal) ---
MENU_MOVIES = "menu.movies"
MENU_AI = "menu.ai"
MENU_MINI_APP = "menu.mini_app"
MENU_PROFILE = "menu.profile"
MENU_PREMIUM = "menu.premium"
MENU_ORDERS = "menu.orders"
MENU_SETTINGS = "menu.settings"

SET_LANG_PREFIX = "setlang:"


def menu_texts(key: str) -> set[str]:
    """Every locale's label for this menu key — what F.text is matched against."""
    return all_translations(key)


def get_main_menu_keyboard(lang: UILanguage) -> ReplyKeyboardMarkup:
    """Persistent bottom reply keyboard shown after /start."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(MENU_MOVIES, lang)), KeyboardButton(text=t(MENU_AI, lang))],
            [KeyboardButton(text=t(MENU_PROFILE, lang)), KeyboardButton(text=t(MENU_PREMIUM, lang))],
            [KeyboardButton(text=t(MENU_ORDERS, lang)), KeyboardButton(text=t(MENU_SETTINGS, lang))],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_mini_app_inline_keyboard(lang: UILanguage) -> InlineKeyboardMarkup:
    """Inline button that launches the Telegram Mini Web App."""
    web_app_url = f"{settings.WEBHOOK_BASE_URL}/miniapp" if settings.WEBHOOK_BASE_URL else "https://example.com"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(MENU_MINI_APP, lang), web_app=WebAppInfo(url=web_app_url))]
        ]
    )


def get_language_keyboard() -> InlineKeyboardMarkup:
    """
    Language picker. Labels are intentionally NOT translated — each option
    is written in its own language, so someone who can't read the current
    one can still find theirs.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("common.lang_uz"), callback_data=f"{SET_LANG_PREFIX}{UILanguage.UZ.value}")],
            [InlineKeyboardButton(text=t("common.lang_ru"), callback_data=f"{SET_LANG_PREFIX}{UILanguage.RU.value}")],
            [InlineKeyboardButton(text=t("common.lang_en"), callback_data=f"{SET_LANG_PREFIX}{UILanguage.EN.value}")],
        ]
    )
