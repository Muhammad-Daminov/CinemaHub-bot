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
MENU_GUIDE = "menu.guide"

# Buttons no longer shown on the reply keyboard. Their handlers, services,
# callbacks and locale strings all remain — this is a **visibility freeze**,
# not a removal, and restoring one is a matter of putting it back in
# `get_main_menu_keyboard`.
#
# They are hidden because the Mini App now does these jobs better than a
# chat can: browsing a catalog, reading a profile, comparing plans and
# scrolling order history are all screens, and the bot was offering a
# worse second copy of each. What the bot is uniquely good at — taking a
# code typed into the chat and handing back a film — is untouched.
#
# Kept as a named list rather than deleted lines so the freeze is
# greppable and the intent survives: see tests/test_bot_menu.py, which
# asserts both that these are absent from the keyboard and that every one
# of their handlers is still registered.
FROZEN_MENU_KEYS = (MENU_MOVIES, MENU_AI, MENU_PROFILE, MENU_PREMIUM, MENU_ORDERS)

SET_LANG_PREFIX = "setlang:"


def menu_texts(key: str) -> set[str]:
    """Every locale's label for this menu key — what F.text is matched against."""
    return all_translations(key)


def mini_app_url() -> str | None:
    """
    Where the Mini App lives, or None when this deployment has no base URL.

    One definition, used by both the inline launcher and the reply
    keyboard's Web App button, so the two cannot drift onto different
    URLs. Returns None rather than a placeholder: Telegram rejects a Web
    App button whose URL is not https, and a button pointing at a stand-in
    domain is worse than no button at all.
    """
    if not settings.WEBHOOK_BASE_URL:
        return None
    return f"{settings.WEBHOOK_BASE_URL}/miniapp"


def get_main_menu_keyboard(lang: UILanguage) -> ReplyKeyboardMarkup:
    """
    Persistent bottom reply keyboard shown after /start.

    Two things only: read the guide, change the language. Everything else
    moved into the Mini App — see FROZEN_MENU_KEYS.

    **The Mini App is deliberately not on this keyboard.** It is opened
    from BotFather's Main App / Menu Button, which Telegram renders in its
    own chrome, and from the inline launcher sent with the guide. A third
    entry point on the reply keyboard only duplicated those and took a row
    from a keyboard whose whole point is being short.

    Nothing about the Mini App itself changes here: `mini_app_url()` and
    `get_mini_app_inline_keyboard` are untouched and still used, so the
    URL and the launcher stay exactly as they were.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(MENU_GUIDE, lang)), KeyboardButton(text=t(MENU_SETTINGS, lang))]
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_mini_app_inline_keyboard(lang: UILanguage) -> InlineKeyboardMarkup:
    """Inline button that launches the Telegram Mini Web App."""
    # Unchanged behaviour: the placeholder is kept here because this
    # keyboard is sent as a message rather than attached to the chat, and
    # existing callers rely on it always returning a markup.
    web_app_url = mini_app_url() or "https://example.com"
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
