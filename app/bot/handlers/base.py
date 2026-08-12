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
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main_menu import (
    MENU_GUIDE,
    MENU_ORDERS,
    MENU_PROFILE,
    MENU_SETTINGS,
    SET_LANG_PREFIX,
    get_language_keyboard,
    get_main_menu_keyboard,
    get_mini_app_inline_keyboard,
    menu_texts,
)
from app.bot.instance import bot
from app.bot.middlewares.access import MEMBERSHIP_CHECK_CALLBACK
from app.core.i18n import t
from app.db.models.user import UILanguage, User
from app.services.achievements import OVERALL, current_ranks, watch_stats
from app.services.membership import check_access, clear_membership_cache
from app.services.payment_history import payment_history
from app.services.settings_store import get_membership_config
from app.services.subscriptions import is_user_premium
from app.services.users import get_or_create_user

router = Router(name="base")


async def _send_main_menu(message: Message, lang: UILanguage, name: str | None) -> None:
    """
    Greeting, then the guide with the Mini App button under it.

    The guide is sent here rather than left to /help because most of the
    product now lives in the Mini App, and a first-time user who is only
    shown a keyboard has no way to discover that a bare code typed into
    the chat finds a film. It is the same `help.text` the 📖 button and
    /help send — one text, so the three cannot disagree.
    """
    await message.answer(
        t("start.welcome", lang, name=name or t("start.friend", lang)),
        reply_markup=get_main_menu_keyboard(lang),
    )
    await message.answer(
        t("help.text", lang),
        parse_mode="HTML",
        reply_markup=get_mini_app_inline_keyboard(lang),
    )


@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject, session: AsyncSession) -> None:
    """
    Welcomes the user and ensures their account exists before showing the main menu.

    The language picker is gated on `language_selected`, not on whether
    the row is new. A user who was shown the picker and closed the chat
    without answering still has a row, so on their next /start they
    counted as existing and went straight to the main menu — in Uzbek,
    the default they never chose, and they were never asked again. Asking
    until the question is actually answered is the whole fix.
    """
    tg_user = message.from_user

    user = await get_or_create_user(session, tg_user.id, tg_user.username, tg_user.full_name, command.args)

    if not user.language_selected:
        # Ask before anything else — the welcome text itself needs a language.
        await message.answer(t("common.choose_language", user.language), reply_markup=get_language_keyboard())
        return

    # `lang` comes from the middleware, which read this same row before the
    # profile refresh above; user.language is the authoritative value here.
    await _send_main_menu(message, user.language, user.full_name)


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


@router.callback_query(F.data == MEMBERSHIP_CHECK_CALLBACK)
async def handle_membership_recheck(callback: CallbackQuery, session: AsyncSession, _) -> None:
    """
    "I have joined" — re-asks Telegram rather than trusting the click.

    The cached yes from the previous check is dropped first, because the
    user pressing this button is telling us the earlier answer is stale.
    """
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        await callback.answer(_("common.need_start"), show_alert=True)
        return

    config = await get_membership_config(session)
    if config.channel:
        await clear_membership_cache(config.channel, user.telegram_id)

    allowed, config = await check_access(session, bot, user)
    if not allowed:
        await callback.answer(_("membership.not_joined"), show_alert=True)
        return

    await callback.message.answer(_("membership.welcome"))
    await _send_main_menu(callback.message, user.language, user.full_name)
    await callback.answer()


# `Command`, not an exact text match: the command is now advertised in
# Telegram's menu, and a menu tap or a group mention can arrive as
# `/help@botname`, which an equality check silently ignores.
@router.message(Command("help"))
async def handle_help(message: Message, _) -> None:
    await message.answer(_("help.text"), parse_mode="HTML")


# The same guide the user was shown at /start, for everyone who skipped or
# forgot it. Deliberately the same locale key rather than a second text:
# two copies of "how this bot works" drift, and the one that drifts is
# always the one nobody re-reads.
#
# `_` is the middleware's translator, already bound to this user's chosen
# language, so the guide follows the language they picked without this
# handler knowing which it is.
@router.message(F.text.in_(menu_texts(MENU_GUIDE)))
async def handle_guide_entry(message: Message, _) -> None:
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


ORDERS_PAGE_SIZE = 10


@router.message(F.text.in_(menu_texts(MENU_ORDERS)))
async def handle_orders_entry(message: Message, session: AsyncSession, _) -> None:
    """
    The user's payment history — the same rows the Mini App shows, read
    through the same service, so the two surfaces cannot disagree about
    what someone has paid. Was a "coming in a later phase" stub while the
    data had existed since Phase 5.
    """
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        await message.answer(_("common.need_start"))
        return

    entries = await payment_history(session, user.id, limit=ORDERS_PAGE_SIZE)
    if not entries:
        await message.answer(_("orders.empty"))
        return

    lines = [_("orders.header")]
    for entry in entries:
        date = entry.created_at.strftime("%d.%m.%Y")
        if entry.is_pending:
            # No sign: nothing has moved yet, and showing "+" would read as
            # money already received.
            lines.append(
                _("orders.line_pending", date=date, amount=f"{entry.amount:.0f}")
            )
        else:
            # Signed, because the ledger is: a purchase is negative and
            # rendering it unsigned would read as a credit.
            lines.append(
                _(
                    "orders.line",
                    date=date,
                    amount=f"{entry.amount:+.0f}",
                    kind=_(f"orders.kind_{entry.kind}"),
                )
            )

    await message.answer("\n".join(lines), parse_mode="HTML")
