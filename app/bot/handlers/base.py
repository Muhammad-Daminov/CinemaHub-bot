"""
Base router — /start, /help, the main menu's top-level entry points, and
language selection.

/start performs the only piece of "real" persistence in this phase:
get-or-create the User row (including referral capture from a deep-link
payload, e.g. https://t.me/bot?start=REF_ABC123). A brand-new user is
asked to pick a language first; returning users go straight to the menu.

Menu buttons are matched with F.text.in_(menu_texts(...)) — the set of
every locale's label — rather than one constant, since the reply
keyboard sends the translated text and a stale keyboard may still show
the previous language.
"""
from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main_menu import (
    MENU_ORDERS,
    MENU_PROFILE,
    MENU_SETTINGS,
    SET_LANG_PREFIX,
    get_language_keyboard,
    get_main_menu_keyboard,
    get_mini_app_inline_keyboard,
    menu_texts,
)
from app.core.i18n import t
from app.db.models.user import UILanguage, User
from app.services.achievements import OVERALL, current_ranks, watch_stats
from app.services.subscriptions import is_user_premium
from app.services.users import get_or_create_user

router = Router(name="base")


async def _send_main_menu(message: Message, lang: UILanguage, name: str | None) -> None:
    await message.answer(
        t("start.welcome", lang, name=name or t("start.friend", lang)),
        reply_markup=get_main_menu_keyboard(lang),
    )
    await message.answer(t("start.quick_access", lang), reply_markup=get_mini_app_inline_keyboard(lang))


@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject, session: AsyncSession, lang: UILanguage) -> None:
    """Welcomes the user and ensures their account exists before showing the main menu."""
    tg_user = message.from_user

    existing = await session.execute(select(User.id).where(User.telegram_id == tg_user.id))
    is_new_user = existing.scalar_one_or_none() is None

    user = await get_or_create_user(session, tg_user.id, tg_user.username, tg_user.full_name, command.args)

    if is_new_user:
        # Ask before anything else — the welcome text itself needs a language.
        await message.answer(t("common.choose_language", user.language), reply_markup=get_language_keyboard())
        return

    await _send_main_menu(message, lang, user.full_name)


@router.callback_query(F.data.startswith(SET_LANG_PREFIX))
async def handle_language_selected(callback: CallbackQuery, session: AsyncSession) -> None:
    chosen = UILanguage(callback.data.removeprefix(SET_LANG_PREFIX))

    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        await callback.answer(t("common.need_start", chosen), show_alert=True)
        return

    user.language = chosen
    # Records that this is a real choice, so the Mini App doesn't ask again.
    user.language_selected = True
    await session.flush()

    await callback.message.answer(t("common.language_saved", chosen))
    await _send_main_menu(callback.message, chosen, user.full_name)
    await callback.answer()


@router.message(F.text == "/help")
async def handle_help(message: Message, _) -> None:
    await message.answer(_("help.text"), parse_mode="HTML")


@router.message(F.text.in_(menu_texts(MENU_SETTINGS)))
async def handle_settings_entry(message: Message, _) -> None:
    await message.answer(_("common.choose_language"), reply_markup=get_language_keyboard())


def _stats_block(stats, ranks, _) -> list[str]:
    """Compact stats + ranks block. Only categories actually watched appear."""
    if stats.total == 0:
        return ["", _("profile.no_history")]

    lines = ["", _("profile.stats_header"), _("profile.stats_total", count=stats.total)]

    for category, count in sorted(stats.by_type.items(), key=lambda pair: -pair[1]):
        line = _("profile.stats_line", label=_(f"catalog.type_{category}"), count=count)
        category_rank = ranks.get(category)
        if category_rank and category_rank.rank_key:
            line += f" — {_(category_rank.rank_key)}"
        lines.append(line)

    overall = ranks[OVERALL]
    if overall.rank_key:
        lines.append(_("profile.rank_line", rank=_(overall.rank_key)))
    lines.append(
        _("profile.rank_max")
        if overall.is_max
        else _("profile.rank_next", remaining=overall.remaining)
    )
    return lines


@router.message(F.text.in_(menu_texts(MENU_PROFILE)))
async def handle_profile_entry(message: Message, session: AsyncSession, _) -> None:
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        await message.answer(_("common.need_start"))
        return

    premium = await is_user_premium(session, user.id)
    stats = await watch_stats(session, user.id)
    ranks = await current_ranks(session, user.id)

    lines = [
        _(
            "profile.text",
            balance=user.balance,
            code=user.referral_code,
            premium="✅" if premium else "❌",
        ),
        *_stats_block(stats, ranks, _),
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.text.in_(menu_texts(MENU_ORDERS)))
async def handle_orders_entry(message: Message, _) -> None:
    await message.answer(_("orders.coming_soon"))
