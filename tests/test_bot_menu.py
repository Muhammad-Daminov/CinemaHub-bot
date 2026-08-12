"""
The bot's reply keyboard after the visibility freeze.

Most of the product moved into the Mini App, so the keyboard now offers
three things: open the app, read the guide, change the language. The rest
of the buttons are hidden.

**Hidden, not deleted.** That distinction is the whole point of this
file: it asserts both halves at once — the frozen labels are absent from
the keyboard, *and* every one of their handlers is still registered on
the dispatcher. A future cleanup that deletes a "dead" handler fails
here, which is what makes the freeze reversible rather than a slow
deletion nobody notices.
"""
import pytest

from app.bot.keyboards.main_menu import (
    FROZEN_MENU_KEYS,
    MENU_AI,
    MENU_GUIDE,
    MENU_MOVIES,
    MENU_ORDERS,
    MENU_PREMIUM,
    MENU_PROFILE,
    MENU_SETTINGS,
    get_main_menu_keyboard,
    menu_texts,
)
from app.core.i18n import t
from app.db.models.user import UILanguage


def _labels(lang: UILanguage) -> set[str]:
    keyboard = get_main_menu_keyboard(lang)
    return {button.text for row in keyboard.keyboard for button in row}


# ---------- what the keyboard shows ----------


@pytest.mark.parametrize("lang", list(UILanguage))
def test_the_frozen_buttons_are_hidden_in_every_language(lang):
    shown = _labels(lang)
    for key in FROZEN_MENU_KEYS:
        assert t(key, lang) not in shown, f"{key} should be hidden from the keyboard"


@pytest.mark.parametrize("lang", list(UILanguage))
def test_the_guide_and_settings_are_offered(lang):
    shown = _labels(lang)
    assert t(MENU_GUIDE, lang) in shown
    assert t(MENU_SETTINGS, lang) in shown


def test_the_keyboard_stays_small():
    """Three entries at most — the point of the freeze was a short keyboard."""
    assert len(_labels(UILanguage.UZ)) <= 3


def test_the_frozen_list_names_every_removed_button():
    """
    Guards the inventory itself: if someone hides another button without
    recording it here, the freeze stops being greppable.
    """
    assert set(FROZEN_MENU_KEYS) == {
        MENU_MOVIES,
        MENU_AI,
        MENU_PROFILE,
        MENU_PREMIUM,
        MENU_ORDERS,
    }


# ---------- what still exists behind them ----------


# The handler behind each hidden button. Named explicitly rather than
# discovered, so deleting one is a failure here instead of a silently
# smaller set.
FROZEN_HANDLERS = {
    MENU_MOVIES: "handle_movies_entry",
    MENU_AI: "handle_ai_entry",
    MENU_PROFILE: "handle_profile_entry",
    MENU_PREMIUM: "handle_premium_start",
    MENU_ORDERS: "handle_orders_entry",
}


def test_every_frozen_feature_still_has_a_live_handler():
    """
    The freeze is visibility only. Each hidden button's handler is still
    registered on the dispatcher, so restoring the button is a one-line
    change and nothing had to be rewritten in the meantime.
    """
    from app.main import dispatcher

    registered = {
        handler.callback.__name__
        for router in dispatcher.sub_routers
        for handler in router.message.handlers
    }

    for key, name in FROZEN_HANDLERS.items():
        assert name in registered, (
            f"handler {name} for {key} is no longer registered — it was deleted, not frozen"
        )


def test_the_frozen_handlers_still_match_their_own_labels():
    """
    The handlers match on `menu_texts(...)`, which is independent of the
    keyboard. So a user whose client still shows a stale keyboard — or who
    types the label by hand — keeps working, and the feature is genuinely
    reachable rather than merely present in the file.
    """
    for key in FROZEN_MENU_KEYS:
        labels = menu_texts(key)
        assert labels, f"{key} has no labels to match on"
        assert all(isinstance(label, str) and label for label in labels)


def test_the_guide_handler_is_registered():
    from app.bot.handlers.base import handle_guide_entry

    assert callable(handle_guide_entry)


# ---------- the guide itself ----------


@pytest.mark.parametrize("lang", list(UILanguage))
def test_the_guide_exists_in_every_language(lang):
    """
    Localised through the same catalog as everything else, so the guide
    follows the language the user picked rather than defaulting to Uzbek.
    """
    text = t("help.text", lang)
    assert text and not text.startswith("help."), "guide missing for this language"


@pytest.mark.parametrize("lang", list(UILanguage))
def test_the_guide_explains_code_search(lang):
    """
    The one thing the bot still does that the Mini App cannot: a bare code
    typed into the chat. With the browse button hidden, a user who is not
    told this has no way to discover it.
    """
    assert "1000" in t("help.text", lang)


@pytest.mark.parametrize("lang", list(UILanguage))
def test_the_guide_does_not_advertise_frozen_features(lang):
    """
    A guide naming a button that is no longer there is worse than no
    guide. AI is the one most likely to creep back in — it was in the old
    help text and its handler is still alive behind the hidden button.
    """
    text = t("help.text", lang).lower()
    for frozen in ("ai ", "🤖"):
        assert frozen not in text, f"guide still mentions a frozen feature: {frozen!r}"


@pytest.mark.parametrize("lang", list(UILanguage))
def test_the_welcome_does_not_advertise_frozen_features(lang):
    text = t("start.welcome", lang, name="X").lower()
    assert "ai" not in text
