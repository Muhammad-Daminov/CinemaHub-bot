"""
Keyboards for catalog browsing.

Callback data format keeps the browsing mode + its argument + the page
number in one compact string so pagination buttons are stateless — no
FSM needed to remember "which genre was I in on page 3".

  cat:pop::2          -> popular, page 2
  cat:gen:Komediya:0  -> genre "Komediya", page 0
  cat:src:venom:1     -> search "venom", page 1
  cat:typ:serial:0    -> content type "serial", page 0

Playback navigation uses its own prefixes, because a title is no longer
always directly playable:

  ttl:12        -> open title 12 (delivers if it's a film, else browses)
  ssn:12:3      -> title 12, season 3 episode list
  eps:12:3:2    -> title 12, season 3, episode page 2
  fav:12        -> toggle title 12 in the viewer's favourites
  watch:<ep_id> -> play THIS episode now (see keyboards/movie.py)
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.movie import WATCH_CALLBACK_PREFIX
from app.core.i18n import t
from app.db.models.content import ContentType, Episode
from app.db.models.user import UILanguage

BROWSE_SEARCH = "cat_menu:search"
BROWSE_GENRES = "cat_menu:genres"
BROWSE_POPULAR = "cat_menu:popular"
BROWSE_CONTINUE = "cat_menu:continue"
BROWSE_COLLECTIONS = "cat_menu:collections"
BROWSE_FAVORITES = "cat_menu:favorites"
BROWSE_MENU_BACK = "cat_menu:back"

PAGE_PREFIX = "cat:"
MODE_POPULAR = "pop"
MODE_GENRE = "gen"
MODE_SEARCH = "src"
MODE_TYPE = "typ"
MODE_COLLECTION = "col"
MODE_FAVORITES = "fav"

TITLE_PREFIX = "ttl:"
SEASON_PREFIX = "ssn:"
EPISODE_PAGE_PREFIX = "eps:"
FAVORITE_PREFIX = "fav:"

NOOP = "cat:noop"

EPISODES_PAGE_SIZE = 8

# Menu order is deliberate: films first, they're the bulk of the catalog.
CONTENT_TYPE_BUTTONS: list[tuple[ContentType, str]] = [
    (ContentType.FILM, "catalog.type_film"),
    (ContentType.SERIAL, "catalog.type_serial"),
    (ContentType.MULTFILM, "catalog.type_multfilm"),
    (ContentType.ANIME, "catalog.type_anime"),
    (ContentType.DRAMA, "catalog.type_drama"),
]


def build_page_callback(mode: str, arg: str, page: int) -> str:
    return f"{PAGE_PREFIX}{mode}:{arg}:{page}"


def parse_page_callback(data: str) -> tuple[str, str, int]:
    """Reverse of build_page_callback -> (mode, arg, page)."""
    _, mode, arg, page = data.split(":", 3)
    return mode, arg, int(page)


def parse_season_callback(data: str) -> tuple[int, int]:
    """ssn:<title_id>:<season> -> (title_id, season)."""
    _, title_id, season = data.split(":", 2)
    return int(title_id), int(season)


def parse_episode_page_callback(data: str) -> tuple[int, int, int]:
    """eps:<title_id>:<season>:<page> -> (title_id, season, page)."""
    _, title_id, season, page = data.split(":", 3)
    return int(title_id), int(season), int(page)


def get_browse_menu_keyboard(lang: UILanguage) -> InlineKeyboardMarkup:
    type_buttons = [
        InlineKeyboardButton(
            text=t(label_key, lang), callback_data=build_page_callback(MODE_TYPE, kind.value, 0)
        )
        for kind, label_key in CONTENT_TYPE_BUTTONS
    ]
    type_rows = [type_buttons[i : i + 2] for i in range(0, len(type_buttons), 2)]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("catalog.btn_search", lang), callback_data=BROWSE_SEARCH)],
            [InlineKeyboardButton(text=t("catalog.btn_genres", lang), callback_data=BROWSE_GENRES)],
            [
                InlineKeyboardButton(
                    text=t("catalog.btn_popular", lang),
                    callback_data=build_page_callback(MODE_POPULAR, "", 0),
                )
            ],
            [InlineKeyboardButton(text=t("catalog.btn_continue", lang), callback_data=BROWSE_CONTINUE)],
            [
                InlineKeyboardButton(
                    text=t("catalog.btn_favorites", lang), callback_data=BROWSE_FAVORITES
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("catalog.btn_collections", lang), callback_data=BROWSE_COLLECTIONS
                )
            ],
            *type_rows,
        ]
    )


def get_genres_keyboard(genres: list[str], lang: UILanguage) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=genre, callback_data=build_page_callback(MODE_GENRE, genre, 0))
        for genre in genres
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=t("catalog.btn_back", lang), callback_data=BROWSE_MENU_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_title_card_keyboard(
    title_id: int, lang: UILanguage, is_favorite: bool = False
) -> InlineKeyboardMarkup:
    """
    Watch + favourite toggle for one title card. Watch opens the title
    rather than playing it — only the handler knows whether this is a
    one-shot film or a serial that needs a season/episode choice first.

    The heart's label carries the current state, so tapping it only needs
    to swap this markup rather than re-send the card.
    """
    heart_key = "catalog.btn_fav_remove" if is_favorite else "catalog.btn_fav_add"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("catalog.btn_watch", lang), callback_data=f"{TITLE_PREFIX}{title_id}"
                ),
                InlineKeyboardButton(
                    text=t(heart_key, lang), callback_data=f"{FAVORITE_PREFIX}{title_id}"
                ),
            ]
        ]
    )


def get_seasons_keyboard(title_id: int, seasons: list[int], lang: UILanguage) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=t("catalog.season_button", lang, season=season),
            callback_data=f"{SEASON_PREFIX}{title_id}:{season}",
        )
        for season in seasons
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text=t("catalog.btn_menu", lang), callback_data=BROWSE_MENU_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_episodes_keyboard(
    title_id: int,
    season: int,
    episodes: list[Episode],
    page: int,
    has_prev: bool,
    has_more: bool,
    show_seasons_back: bool,
    lang: UILanguage,
) -> InlineKeyboardMarkup:
    """Episode buttons play immediately; nav row pages within the season."""
    buttons = [
        InlineKeyboardButton(
            text=t("catalog.episode_button", lang, number=episode.number),
            callback_data=f"{WATCH_CALLBACK_PREFIX}{episode.id}",
        )
        for episode in episodes
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]

    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(
            InlineKeyboardButton(
                text="◀️", callback_data=f"{EPISODE_PAGE_PREFIX}{title_id}:{season}:{page - 1}"
            )
        )
    nav.append(InlineKeyboardButton(text=f"· {page + 1} ·", callback_data=NOOP))
    if has_more:
        nav.append(
            InlineKeyboardButton(
                text="▶️", callback_data=f"{EPISODE_PAGE_PREFIX}{title_id}:{season}:{page + 1}"
            )
        )
    if len(nav) > 1:
        rows.append(nav)

    if show_seasons_back:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("catalog.btn_seasons_back", lang),
                    callback_data=f"{TITLE_PREFIX}{title_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=t("catalog.btn_menu", lang), callback_data=BROWSE_MENU_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_collections_keyboard(summaries, lang: UILanguage) -> InlineKeyboardMarkup:
    """
    One button per collection. Paging into a collection reuses the standard
    `cat:<mode>:<arg>:<page>` format with mode "col" and the collection id as
    the arg — no parallel callback scheme to maintain.
    """
    rows = [
        [
            InlineKeyboardButton(
                text=f"{item.collection.name} ({item.title_count})",
                callback_data=build_page_callback(MODE_COLLECTION, str(item.collection.id), 0),
            )
        ]
        for item in summaries
    ]
    rows.append([InlineKeyboardButton(text=t("catalog.btn_back", lang), callback_data=BROWSE_MENU_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_continue_watching_keyboard(items, lang: UILanguage) -> InlineKeyboardMarkup:
    """
    One button per recently-watched episode, reusing the existing
    `watch:` callback so tapping plays it exactly as it would from the
    catalog — no second delivery path to keep in sync.
    """
    rows = []
    for item in items:
        if item.title.is_single_episode:
            label = t("catalog.continue_item_film", lang, name=item.title.name)
        else:
            label = t(
                "catalog.continue_item_serial",
                lang,
                name=item.title.name,
                season=item.episode.season,
                number=item.episode.number,
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"{WATCH_CALLBACK_PREFIX}{item.episode.id}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=t("catalog.btn_menu", lang), callback_data=BROWSE_MENU_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_pagination_keyboard(
    mode: str, arg: str, page: int, has_prev: bool, has_more: bool, lang: UILanguage
) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=build_page_callback(mode, arg, page - 1)))
    nav.append(InlineKeyboardButton(text=f"· {page + 1} ·", callback_data=NOOP))
    if has_more:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=build_page_callback(mode, arg, page + 1)))

    return InlineKeyboardMarkup(
        inline_keyboard=[
            nav,
            [InlineKeyboardButton(text=t("catalog.btn_menu", lang), callback_data=BROWSE_MENU_BACK)],
        ]
    )
