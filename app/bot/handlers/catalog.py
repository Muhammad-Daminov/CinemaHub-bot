"""
Catalog browsing handler.

Title cards are sent as separate messages (photo when a poster exists,
text otherwise) followed by one pagination message. Paging deletes the
previous batch before sending the next so the chat doesn't fill up
with stale pages.

Tapping a card opens the title rather than playing it: a film has one
episode and is delivered straight away, while a serial routes through
a season picker and a paginated episode list. Some serials here run to
140+ episodes, so that list is always paged, never dumped.

Queries go through app.services.content — the single catalog query
layer. There is deliberately no separate catalog service.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.streaming import deliver_and_warn
from app.bot.keyboards.catalog import (
    BROWSE_COLLECTIONS,
    BROWSE_CONTINUE,
    BROWSE_FAVORITES,
    BROWSE_GENRES,
    BROWSE_MENU_BACK,
    BROWSE_SEARCH,
    EPISODE_PAGE_PREFIX,
    EPISODES_PAGE_SIZE,
    FAVORITE_PREFIX,
    MODE_COLLECTION,
    MODE_FAVORITES,
    MODE_GENRE,
    MODE_SEARCH,
    MODE_TYPE,
    NOOP,
    PAGE_PREFIX,
    SEASON_PREFIX,
    TITLE_PREFIX,
    get_browse_menu_keyboard,
    get_collections_keyboard,
    get_continue_watching_keyboard,
    get_episodes_keyboard,
    get_genres_keyboard,
    get_pagination_keyboard,
    get_seasons_keyboard,
    get_title_card_keyboard,
    parse_episode_page_callback,
    parse_page_callback,
    parse_season_callback,
)
from app.bot.keyboards.main_menu import MENU_MOVIES, menu_texts
from app.db.models.content import ContentType, Title
from app.db.models.user import UILanguage
from app.services.achievements import continue_watching
from app.services.content import TitlePage, content_service
from app.services.users import get_user_id

router = Router(name="catalog")


class CatalogStates(StatesGroup):
    awaiting_search_query = State()


def _format_card(title: Title, _) -> str:
    lines = [f"🎬 <b>{title.name}</b>"]
    meta: list[str] = []
    if title.year:
        meta.append(str(title.year))
    if title.rating:
        meta.append(f"⭐ {title.rating}")
    if not title.is_single_episode:
        meta.append(_("catalog.serial_badge"))
    if meta:
        lines.append(" · ".join(meta))
    if title.genres:
        # Stored value is a canonical key ("science_fiction"); the hashtag
        # shows the viewer's own label for it.
        lines.append(" ".join(f"#{_(f'genre.{g}')}" for g in title.genres))
    return "\n".join(lines)


async def _send_page(
    message: Message,
    session: AsyncSession,
    user_id: int | None,
    page: TitlePage,
    mode: str,
    arg: str,
    empty_text: str,
    lang: UILanguage,
    _,
) -> None:
    if not page.titles:
        await message.answer(empty_text, reply_markup=get_browse_menu_keyboard(lang))
        return

    # One query for the whole page rather than a lookup per card.
    saved: set[int] = (
        await content_service.favorite_title_ids(session, user_id, [item.id for item in page.titles])
        if user_id
        else set()
    )

    for title in page.titles:
        caption = _format_card(title, _)
        keyboard = get_title_card_keyboard(title.id, lang, is_favorite=title.id in saved)
        if title.poster_url:
            await message.answer_photo(title.poster_url, caption=caption, reply_markup=keyboard)
        else:
            await message.answer(caption, reply_markup=keyboard)

    await message.answer(
        _("catalog.page_label"),
        reply_markup=get_pagination_keyboard(mode, arg, page.page, page.has_prev, page.has_more, lang),
    )


async def _load_page(
    session: AsyncSession, mode: str, arg: str, page_num: int, user_id: int | None
) -> TitlePage:
    if mode == MODE_SEARCH:
        return await content_service.browse(session, page=page_num, query=arg)
    if mode == MODE_GENRE:
        return await content_service.browse(session, page=page_num, genre=arg)
    if mode == MODE_TYPE:
        return await content_service.browse(session, page=page_num, content_type=ContentType(arg))
    if mode == MODE_COLLECTION:
        return await content_service.browse(session, page=page_num, collection_id=int(arg))
    if mode == MODE_FAVORITES:
        if user_id is None:
            return TitlePage(titles=[], page=page_num, has_more=False)
        return await content_service.list_favorites(session, user_id, page=page_num)
    return await content_service.browse(session, page=page_num)


async def _show_episode_page(
    message: Message, session: AsyncSession, title_id: int, season: int, page: int, lang: UILanguage, _
) -> bool:
    """Renders one page of a season's episodes. False if the season is empty."""
    episodes = await content_service.list_episodes(session, title_id, season)
    if not episodes:
        return False

    seasons = await content_service.list_seasons(session, title_id)
    start = page * EPISODES_PAGE_SIZE
    window = episodes[start : start + EPISODES_PAGE_SIZE]
    if not window:
        return False

    title = await session.get(Title, title_id)
    season_suffix = _("catalog.season_suffix", season=season) if len(seasons) > 1 else ""

    await message.answer(
        _("catalog.episodes_prompt", name=title.name if title else "", season_suffix=season_suffix),
        reply_markup=get_episodes_keyboard(
            title_id=title_id,
            season=season,
            episodes=window,
            page=page,
            has_prev=page > 0,
            has_more=start + EPISODES_PAGE_SIZE < len(episodes),
            show_seasons_back=len(seasons) > 1,
            lang=lang,
        ),
    )
    return True


@router.message(F.text.in_(menu_texts(MENU_MOVIES)))
async def handle_movies_entry(message: Message, state: FSMContext, lang: UILanguage, _) -> None:
    await state.clear()
    await message.answer(_("catalog.menu_prompt"), reply_markup=get_browse_menu_keyboard(lang))


@router.callback_query(F.data == BROWSE_MENU_BACK)
async def handle_browse_back(callback: CallbackQuery, state: FSMContext, lang: UILanguage, _) -> None:
    await state.clear()
    await callback.message.answer(_("catalog.menu_prompt"), reply_markup=get_browse_menu_keyboard(lang))
    await callback.answer()


@router.callback_query(F.data == BROWSE_SEARCH)
async def handle_search_start(callback: CallbackQuery, state: FSMContext, _) -> None:
    await state.set_state(CatalogStates.awaiting_search_query)
    await callback.message.answer(_("catalog.search_prompt"))
    await callback.answer()


@router.message(CatalogStates.awaiting_search_query, F.text)
async def handle_search_query(
    message: Message, state: FSMContext, session: AsyncSession, lang: UILanguage, _
) -> None:
    query = message.text.strip()
    await state.clear()
    page = await content_service.browse(session, page=0, query=query)
    await _send_page(
        message,
        session,
        await get_user_id(session, message.from_user.id),
        page,
        MODE_SEARCH,
        query,
        _("catalog.not_found", query=query),
        lang,
        _,
    )


@router.callback_query(F.data == BROWSE_GENRES)
async def handle_genres(callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _) -> None:
    genres = await content_service.list_genres(session)
    if not genres:
        await callback.answer(_("catalog.no_genres"), show_alert=True)
        return
    await callback.message.answer(_("catalog.choose_genre"), reply_markup=get_genres_keyboard(genres, lang))
    await callback.answer()


@router.callback_query(F.data == BROWSE_COLLECTIONS)
async def handle_collections(
    callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _
) -> None:
    summaries = [s for s in await content_service.list_collections(session) if s.title_count > 0]
    if not summaries:
        await callback.answer(_("catalog.no_collections"), show_alert=True)
        return
    await callback.message.answer(
        _("catalog.choose_collection"), reply_markup=get_collections_keyboard(summaries, lang)
    )
    await callback.answer()


@router.callback_query(F.data == BROWSE_CONTINUE)
async def handle_continue_watching(
    callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _
) -> None:
    user_id = await get_user_id(session, callback.from_user.id)

    items = await continue_watching(session, user_id) if user_id else []
    if not items:
        await callback.answer(_("catalog.continue_empty"), show_alert=True)
        return

    await callback.message.answer(
        _("catalog.continue_header"),
        reply_markup=get_continue_watching_keyboard(items, lang),
    )
    await callback.answer()


@router.callback_query(F.data == BROWSE_FAVORITES)
async def handle_favorites(
    callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _
) -> None:
    user_id = await get_user_id(session, callback.from_user.id)
    page = await _load_page(session, MODE_FAVORITES, "", 0, user_id)
    if not page.titles:
        await callback.answer(_("catalog.favorites_empty"), show_alert=True)
        return

    await callback.message.answer(_("catalog.favorites_header"))
    await _send_page(
        callback.message,
        session,
        user_id,
        page,
        MODE_FAVORITES,
        "",
        _("catalog.favorites_empty"),
        lang,
        _,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(FAVORITE_PREFIX))
async def handle_favorite_toggle(
    callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _
) -> None:
    """
    Flips the heart on the card that was tapped and swaps that one button
    in place — re-sending the card would push the whole page up the chat.
    """
    title_id = int(callback.data.removeprefix(FAVORITE_PREFIX))
    user_id = await get_user_id(session, callback.from_user.id)
    if user_id is None:
        await callback.answer(_("common.need_start"), show_alert=True)
        return

    saved = await content_service.toggle_favorite(session, user_id, title_id)

    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_title_card_keyboard(title_id, lang, is_favorite=saved)
        )
    except Exception:  # noqa: BLE001 — card may be too old to edit; the toast still confirms it
        pass

    await callback.answer(_("catalog.favorite_added" if saved else "catalog.favorite_removed"))


@router.callback_query(F.data == NOOP)
async def handle_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith(PAGE_PREFIX))
async def handle_page(callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _) -> None:
    mode, arg, page_num = parse_page_callback(callback.data)
    user_id = await get_user_id(session, callback.from_user.id)
    page = await _load_page(session, mode, arg, page_num, user_id)

    # Remove the pagination message the user just tapped, so successive
    # pages don't stack up as dead controls in the chat.
    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001 — message may already be gone; not worth failing the page load
        pass

    empty = _("catalog.empty_page")
    await _send_page(callback.message, session, user_id, page, mode, arg, empty, lang, _)
    await callback.answer()


@router.callback_query(F.data.startswith(TITLE_PREFIX))
async def handle_open_title(callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _) -> None:
    """Films play immediately; serials open their season or episode picker."""
    title_id = int(callback.data.removeprefix(TITLE_PREFIX))
    title = await session.get(Title, title_id)
    if title is None:
        await callback.answer(_("catalog.title_not_found"), show_alert=True)
        return

    episodes = await content_service.list_episodes(session, title_id)
    if not episodes:
        await callback.answer(_("catalog.not_available"), show_alert=True)
        return

    if title.is_single_episode:
        result = await deliver_and_warn(
            callback.bot, session, callback.message.chat.id, callback.from_user.id, episodes[0], lang, _
        )
        if result is None:
            await callback.answer(_("streaming.send_error"), show_alert=True)
            return
        await callback.answer()
        return

    seasons = await content_service.list_seasons(session, title_id)
    if len(seasons) > 1:
        await callback.message.answer(
            _("catalog.choose_season", name=title.name),
            reply_markup=get_seasons_keyboard(title_id, seasons, lang),
        )
        await callback.answer()
        return

    await _show_episode_page(callback.message, session, title_id, seasons[0], 0, lang, _)
    await callback.answer()


@router.callback_query(F.data.startswith(SEASON_PREFIX))
async def handle_season(callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _) -> None:
    title_id, season = parse_season_callback(callback.data)
    if not await _show_episode_page(callback.message, session, title_id, season, 0, lang, _):
        await callback.answer(_("catalog.no_episodes_in_season"), show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith(EPISODE_PAGE_PREFIX))
async def handle_episode_page(
    callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _
) -> None:
    title_id, season, page = parse_episode_page_callback(callback.data)

    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001 — same rationale as handle_page
        pass

    if not await _show_episode_page(callback.message, session, title_id, season, page, lang, _):
        await callback.answer(_("catalog.no_episodes_on_page"), show_alert=True)
        return
    await callback.answer()
