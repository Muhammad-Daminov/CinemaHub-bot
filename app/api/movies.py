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
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.bot.instance import bot
from app.db.models.content import Collection, Episode, MediaFile, Title, title_collections
from app.db.models.user import User
from app.db.session import get_db_session
from app.services.content import content_service
from app.services.streaming import streaming_service

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


class WatchResponse(BaseModel):
    status: str
    message: str


class CollectionOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    poster_url: str | None
    title_count: int


def _to_movie_out(title: Title) -> MovieOut:
    return MovieOut(
        id=title.id,
        title=title.name,
        year=title.year,
        genres=title.genres,
        poster_url=title.poster_url,
        description=title.description,
        rating=float(title.rating) if title.rating is not None else None,
        view_count=title.view_count,
    )


def _playable_titles() -> Select:
    """
    Active titles that have at least one deliverable file. EXISTS rather
    than a relationship walk — this runs under async SQLAlchemy, where a
    lazy load raises MissingGreenlet.
    """
    has_file = (
        select(MediaFile.id)
        .join(Episode, Episode.id == MediaFile.episode_id)
        .where(Episode.title_id == Title.id)
        .exists()
    )
    return select(Title).where(Title.is_active.is_(True), has_file)


@router.get("/collections", response_model=list[CollectionOut])
async def list_collections(
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[CollectionOut]:
    """Active collections only — the admin panel is where inactive ones live."""
    summaries = await content_service.list_collections(session)
    return [
        CollectionOut(
            id=item.collection.id,
            name=item.collection.name,
            slug=item.collection.slug,
            description=item.collection.description,
            poster_url=item.collection.poster_url,
            title_count=item.title_count,
        )
        for item in summaries
    ]


@router.get("", response_model=list[MovieOut])
async def list_movies(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    genre: str | None = None,
    collection_id: int | None = None,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[MovieOut]:
    stmt = _playable_titles().order_by(Title.created_at.desc())
    if genre:
        stmt = stmt.where(Title.genres.any(genre))
    if collection_id is not None:
        # Explicit join, never a lazy Title.collections walk.
        stmt = stmt.join(
            title_collections, title_collections.c.title_id == Title.id
        ).where(title_collections.c.collection_id == collection_id)
    result = await session.execute(stmt.offset(skip).limit(limit))
    return [_to_movie_out(title) for title in result.scalars()]


@router.get("/top", response_model=list[MovieOut])
async def top_movies(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[MovieOut]:
    result = await session.execute(
        _playable_titles().order_by(Title.view_count.desc()).limit(limit)
    )
    return [_to_movie_out(title) for title in result.scalars()]


@router.get("/search", response_model=list[MovieOut])
async def search_movies(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> list[MovieOut]:
    result = await session.execute(
        _playable_titles()
        .where(Title.name.ilike(f"%{q}%"))
        .order_by(Title.view_count.desc())
        .limit(limit)
    )
    return [_to_movie_out(title) for title in result.scalars()]


@router.get("/{movie_id}", response_model=MovieOut)
async def get_movie(
    movie_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_current_user),
) -> MovieOut:
    title = await session.get(Title, movie_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return _to_movie_out(title)


@router.post("/{movie_id}/watch", response_model=WatchResponse)
async def watch_movie(
    movie_id: int,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> WatchResponse:
    title = await session.get(Title, movie_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Movie not available")

    # The Mini App lists titles, not episodes — deliver the first one.
    episodes = await content_service.list_episodes(session, movie_id)
    if not episodes:
        raise HTTPException(status_code=404, detail="Movie not available")

    media_file = await content_service.pick_file(session, episodes[0].id, user.language)
    if media_file is None:
        raise HTTPException(status_code=404, detail="Movie not available")

    try:
        await streaming_service.deliver_episode(
            bot=bot,
            session=session,
            chat_id=user.telegram_id,
            title=title,
            episode=episodes[0],
            media_file=media_file,
            lang=user.language,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return WatchResponse(status="sent", message="Check your chat with the bot — the video is on its way.")
