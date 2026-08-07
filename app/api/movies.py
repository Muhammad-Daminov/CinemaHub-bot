"""
Catalog REST API for the Mini App.

Reads the Title/Episode/MediaFile schema, but keeps the response shape
the Mini App already expects — `title` here is Title.name — so the
frontend contract is unchanged.

The Mini App itself never receives video bytes or a file_id: /watch
just tells the bot to deliver the episode into the user's own chat with
CinemaHub Pro, matching Telegram's model where a WebApp can't stream
media inline.
"""
import logging

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.bot.instance import bot
from app.core.i18n import t
from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Title,
    title_collections,
)
from app.db.models.user import User
from app.db.session import get_db_session
from app.services.achievements import continue_watching
from app.services.content import _has_playable_file, content_service
from app.services.images import get_image
from app.services.streaming import streaming_service

logger = logging.getLogger(__name__)

router = APIRouter()


class MovieOut(BaseModel):
    id: int
    title: str
    year: int | None
    genres: list[str] | None
    poster_url: str | None
    description: str | None
    rating: float | None
    view_count: int
    # How many episodes this title has. A film has 1. The Mini App uses it
    # to decide whether a play control may start playback directly or must
    # open the episode selector first — without it, any "Watch" button on a
    # serial silently starts episode 1.
    episode_count: int = 1


class WatchResponse(BaseModel):
    status: str
    message: str


class EpisodeOut(BaseModel):
    id: int
    season: int
    number: int
    name: str | None
    duration_minutes: int | None
    # Which audio tracks this specific episode has. Per-episode rather than
    # per-title because a serial can be dubbed only partway through, and
    # showing the title's full set would promise tracks that aren't there.
    audio_languages: list[AudioLanguage]
    watched: bool


class EpisodePageOut(BaseModel):
    episodes: list[EpisodeOut]
    page: int
    has_more: bool


class SearchResultOut(BaseModel):
    """
    Search hits across both kinds of thing a query can name.

    Collections are returned alongside titles because a user typing
    "Marvel" means the franchise, not one film whose name happens to
    contain the word — and previously they got only the latter.
    """

    titles: list["MovieOut"]
    collections: list["CollectionOut"]


class CollectionOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    poster_url: str | None
    title_count: int


def _collection_out(item) -> "CollectionOut":
    """Shared so the two endpoints returning collections cannot drift."""
    collection = item.collection
    return CollectionOut(
        id=collection.id,
        name=collection.name,
        slug=collection.slug,
        description=collection.description,
        poster_url=(
            f"/api/movies/images/{collection.poster_image_id}"
            if collection.poster_image_id
            else collection.poster_url
        ),
        title_count=item.title_count,
    )


def _poster_for(title: Title) -> str | None:
    """
    An uploaded poster wins over TMDB's.

    Returned as a URL to our own endpoint rather than raw bytes so the
    client caches it like any other image, and so clearing the upload
    falls straight back to `poster_url` with no client change.
    """
    if title.poster_image_id:
        return f"/api/movies/images/{title.poster_image_id}"
    return title.poster_url


def _to_movie_out(title: Title, episode_count: int = 1) -> MovieOut:
    return MovieOut(
        id=title.id,
        title=title.name,
        year=title.year,
        genres=title.genres,
        poster_url=_poster_for(title),
        description=title.description,
        rating=float(title.rating) if title.rating is not None else None,
        view_count=title.view_count,
        episode_count=episode_count,
    )


async def _to_movie_outs(session: AsyncSession, titles: list[Title]) -> list[MovieOut]:
    """Serializes a list of titles, resolving episode counts in one query."""
    counts = await content_service.episode_counts(session, [t.id for t in titles])
    return [_to_movie_out(title, counts.get(title.id, 1)) for title in titles]


def _playable_titles(audio_language: AudioLanguage | None = None) -> Select:
    """
    Active titles that have at least one deliverable file, optionally
    narrowed to a specific audio language.

    Uses content._has_playable_file so the API and the bot ask the exact
    same question — this file used to carry its own copy of that EXISTS,
    which is how two catalog gates drift apart.
    """
    return select(Title).where(Title.is_active.is_(True), _has_playable_file(audio_language))


@router.get("/images/{image_id}")
async def get_public_image(
    image_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> Response:
    """
    Serves an uploaded poster.

    Behind auth like the rest of the catalog, but not per-owner: a poster
    is public artwork within the app, unlike a payment receipt.
    """
    image = await get_image(session, image_id)
    if image is None or image.data is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=image.data,
        media_type=image.content_type,
        # Posters change rarely and are requested constantly.
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/collections", response_model=list[CollectionOut])
async def list_collections(
    q: str | None = Query(default=None, description="Filters collections by name"),
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[CollectionOut]:
    """Active collections only — the admin panel is where inactive ones live."""
    summaries = await content_service.list_collections(session)
    if q:
        needle = q.strip().lower()
        summaries = [s for s in summaries if needle in s.collection.name.lower()]
    return [_collection_out(item) for item in summaries]


@router.get("", response_model=list[MovieOut])
async def list_movies(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    genre: str | None = None,
    collection_id: int | None = None,
    content_type: ContentType | None = None,
    audio_language: AudioLanguage | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[MovieOut]:
    """Newest first. Every home row that is just a filtered slice comes through here."""
    stmt = _playable_titles(audio_language).order_by(Title.created_at.desc())
    if genre:
        stmt = stmt.where(Title.genres.any(genre))
    if content_type is not None:
        stmt = stmt.where(Title.content_type == content_type)
    if collection_id is not None:
        # Explicit join, never a lazy Title.collections walk.
        stmt = stmt.join(
            title_collections, title_collections.c.title_id == Title.id
        ).where(title_collections.c.collection_id == collection_id)
    result = await session.execute(stmt.offset(skip).limit(limit))
    return await _to_movie_outs(session, list(result.scalars()))


@router.get("/top", response_model=list[MovieOut])
async def top_movies(
    limit: int = Query(default=10, ge=1, le=50),
    audio_language: AudioLanguage | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[MovieOut]:
    result = await session.execute(
        _playable_titles(audio_language).order_by(Title.view_count.desc()).limit(limit)
    )
    return await _to_movie_outs(session, list(result.scalars()))


@router.get("/search/all", response_model=SearchResultOut)
async def search_everything(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    audio_language: AudioLanguage | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> SearchResultOut:
    """
    Titles *and* collections matching `q`.

    Separate from /search, which stays title-only so existing clients keep
    the response shape they parse.
    """
    result = await session.execute(
        _playable_titles(audio_language)
        .where(Title.name.ilike(f"%{q}%"))
        .order_by(Title.view_count.desc())
        .limit(limit)
    )
    titles = await _to_movie_outs(session, list(result.scalars()))

    needle = q.strip().lower()
    summaries = [
        item
        for item in await content_service.list_collections(session)
        if needle in item.collection.name.lower()
    ]
    return SearchResultOut(titles=titles, collections=[_collection_out(item) for item in summaries])


@router.get("/search", response_model=list[MovieOut])
async def search_movies(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    audio_language: AudioLanguage | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[MovieOut]:
    result = await session.execute(
        _playable_titles(audio_language)
        .where(Title.name.ilike(f"%{q}%"))
        .order_by(Title.view_count.desc())
        .limit(limit)
    )
    return await _to_movie_outs(session, list(result.scalars()))


# Declared before /{movie_id}: FastAPI matches in definition order, so a
# route added after it would be swallowed as a movie_id and 422.
@router.get("/recommended", response_model=list[MovieOut])
async def recommended_movies(
    limit: int = Query(default=10, ge=1, le=50),
    audio_language: AudioLanguage | None = None,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> list[MovieOut]:
    """Personalised row. Falls back to popularity for a user with no history."""
    titles = await content_service.recommended_for_user(
        session, user.id, limit=limit, audio_language=audio_language
    )
    return await _to_movie_outs(session, titles)


@router.get("/continue", response_model=list[MovieOut])
async def continue_watching_movies(
    limit: int = Query(default=10, ge=1, le=50),
    audio_language: AudioLanguage | None = None,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> list[MovieOut]:
    """
    Most recently watched, newest first.

    continue_watching() is per *episode*, so a serial appears once per
    episode watched; the home row wants one card per title.
    """
    items = await continue_watching(
        session, user.id, limit=limit, audio_language=audio_language
    )
    seen: set[int] = set()
    titles: list[Title] = []
    for item in items:
        if item.title.id in seen:
            continue
        seen.add(item.title.id)
        titles.append(item.title)
    return await _to_movie_outs(session, titles)


@router.get("/{movie_id}/similar", response_model=list[MovieOut])
async def similar_movies(
    movie_id: int,
    limit: int = Query(default=10, ge=1, le=50),
    audio_language: AudioLanguage | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[MovieOut]:
    titles = await content_service.similar_titles(
        session, movie_id, limit=limit, audio_language=audio_language
    )
    return await _to_movie_outs(session, titles)


@router.get("/{movie_id}", response_model=MovieOut)
async def get_movie(
    movie_id: int,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> MovieOut:
    title = await session.get(Title, movie_id)
    if title is None:
        raise HTTPException(status_code=404, detail=t("catalog.title_not_found", user.language))
    counts = await content_service.episode_counts(session, [title.id])
    return _to_movie_out(title, counts.get(title.id, 1))


@router.get("/{movie_id}/seasons", response_model=list[int])
async def list_title_seasons(
    movie_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[int]:
    """
    Season numbers for a title, ascending.

    A film is a Title with a single Episode, so this returns `[1]` for one
    — the client decides whether a season control is worth showing from
    the length of this list, not from the content type.
    """
    return await content_service.list_seasons(session, movie_id)


@router.get("/{movie_id}/episodes", response_model=EpisodePageOut)
async def list_title_episodes(
    movie_id: int,
    season: int | None = None,
    page: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> EpisodePageOut:
    """
    One page of a title's episodes, each with its audio tracks and whether
    the viewer has already watched it.

    Audio languages and watched state are resolved in two batch queries
    rather than per episode — the list is the one screen where an N+1
    would be most visible.
    """
    episodes, has_more = await content_service.episode_page(
        session, movie_id, season=season, page=page
    )
    episode_ids = [episode.id for episode in episodes]

    languages = await content_service.languages_by_episode(session, episode_ids)
    watched = await content_service.watched_episode_ids(session, user.id, episode_ids)

    return EpisodePageOut(
        episodes=[
            EpisodeOut(
                id=episode.id,
                season=episode.season,
                number=episode.number,
                name=episode.name,
                duration_minutes=episode.duration_minutes,
                audio_languages=languages.get(episode.id, []),
                watched=episode.id in watched,
            )
            for episode in episodes
        ],
        page=page,
        has_more=has_more,
    )


@router.post("/{movie_id}/watch", response_model=WatchResponse)
async def watch_movie(
    movie_id: int,
    episode_id: int | None = None,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> WatchResponse:
    # Every string returned from here is rendered straight into a toast by
    # the Mini App (App.tsx reads `detail` and `message` verbatim), so it
    # has to arrive already translated into the caller's language.
    unavailable = t("streaming.not_available", user.language)

    title = await session.get(Title, movie_id)
    if title is None:
        raise HTTPException(status_code=404, detail=unavailable)

    if episode_id is None:
        # A film has exactly one episode, so omitting the id is unambiguous
        # and still works. A serial has many, and silently starting the
        # first one is wrong however the request arrived — the caller might
        # be resuming at episode 40. Refused here rather than only in the
        # UI, so the guarantee does not depend on which client is calling.
        counts = await content_service.episode_counts(session, [movie_id])
        if counts.get(movie_id, 1) > 1:
            raise HTTPException(
                status_code=422, detail=t("app.episode_required", user.language)
            )
        episode = await content_service.first_episode(session, movie_id)
    else:
        # Resolved through the title, so an episode id belonging to some
        # other title cannot be used to fetch content this call didn't ask for.
        episode = await content_service.get_episode_of_title(session, movie_id, episode_id)

    if episode is None:
        raise HTTPException(status_code=404, detail=unavailable)

    media_file = await content_service.pick_file(session, episode.id, user.language)
    if media_file is None:
        raise HTTPException(status_code=404, detail=unavailable)

    try:
        await streaming_service.deliver_episode(
            bot=bot,
            session=session,
            chat_id=user.telegram_id,
            title=title,
            episode=episode,
            media_file=media_file,
            lang=user.language,
            user_id=user.id,
        )
    except ValueError as exc:
        # The raised text is an internal diagnostic naming the MediaFile row;
        # it was previously passed straight through to the user's screen.
        # Log it, show them something they can act on.
        logger.warning("watch delivery failed for title %s: %s", movie_id, exc)
        raise HTTPException(
            status_code=409, detail=t("streaming.send_error", user.language)
        ) from exc

    return WatchResponse(status="sent", message=t("app.watch_sent", user.language))
