"""
Dominant-interest ordering in the recommendation feed.

The rule is **prioritisation, not filtering**: a mostly-anime viewer sees
anime first and still sees everything else. Removing other content would
trap someone in whatever they happened to watch most.

Phase 9B's profile is the only source of "dominant"; this feed does not
re-derive it, and the isolation test proves one user's interests cannot
reorder another's feed.
"""
from datetime import datetime, timezone

import pytest

from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Episode,
    MediaFile,
    Title,
    VideoQuality,
    WatchHistory,
)
from app.services.content import content_service
from app.services.personalization import get_profile
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _title(session, name, content_type, views=0) -> Title:
    title = Title(content_type=content_type, name=name, is_active=True, view_count=views)
    session.add(title)
    await session.flush()
    episode = Episode(title_id=title.id, season=1, number=1)
    session.add(episode)
    await session.flush()
    session.add(
        MediaFile(
            episode_id=episode.id,
            file_id=f"f{episode.id}",
            language=AudioLanguage.UZ_DUB,
            quality=VideoQuality.HD_720,
        )
    )
    await session.flush()
    return title


async def _watch(session, user, content_type, count):
    for index in range(count):
        title = await _title(session, f"seen-{content_type.value}-{user.id}-{index}", content_type)
        session.add(
            WatchHistory(
                user_id=user.id,
                title_id=title.id,
                episode_id=(
                    await session.execute(
                        __import__("sqlalchemy").select(Episode.id).where(Episode.title_id == title.id)
                    )
                ).scalar_one(),
                watch_count=1,
                last_watched_at=datetime.now(timezone.utc),
            )
        )
    await session.flush()


async def _recommend(session, user, limit=20):
    profile = await get_profile(session, user.id)
    dominant = ContentType(profile.dominant_type) if profile.dominant_type else None
    return await content_service.recommended_for_user(
        session, user.id, limit=limit, dominant_type=dominant
    )


async def test_the_dominant_type_leads_the_feed(db_session):
    user = await make_user(db_session, 9301)
    await _watch(db_session, user, ContentType.ANIME, 12)
    # Unwatched candidates, with films deliberately more popular so only
    # the interest ordering can put anime first.
    await _title(db_session, "Popular film", ContentType.FILM, views=9999)
    await _title(db_session, "Fresh anime", ContentType.ANIME, views=1)

    feed = await _recommend(db_session, user)
    assert feed[0].content_type == ContentType.ANIME


async def test_other_content_types_remain_present(db_session):
    """Prioritisation, not an exclusive filter."""
    user = await make_user(db_session, 9302)
    await _watch(db_session, user, ContentType.ANIME, 12)
    await _title(db_session, "A film", ContentType.FILM, views=50)
    await _title(db_session, "More anime", ContentType.ANIME, views=1)

    types = {title.content_type for title in await _recommend(db_session, user)}
    assert ContentType.FILM in types, "other types must still be reachable"
    assert ContentType.ANIME in types


async def test_a_user_without_a_dominant_type_orders_by_popularity(db_session):
    """Unchanged behaviour for a viewer with no clear interest."""
    user = await make_user(db_session, 9303)
    await _title(db_session, "Popular", ContentType.FILM, views=9999)
    await _title(db_session, "Unpopular", ContentType.ANIME, views=1)

    feed = await _recommend(db_session, user)
    assert feed[0].name == "Popular"


async def test_two_users_get_feeds_ordered_by_their_own_interests(db_session):
    anime_fan = await make_user(db_session, 9304)
    film_fan = await make_user(db_session, 9305)
    await _watch(db_session, anime_fan, ContentType.ANIME, 12)
    await _watch(db_session, film_fan, ContentType.FILM, 12)
    await _title(db_session, "Shared film", ContentType.FILM, views=500)
    await _title(db_session, "Shared anime", ContentType.ANIME, views=500)

    assert (await _recommend(db_session, anime_fan))[0].content_type == ContentType.ANIME
    assert (await _recommend(db_session, film_fan))[0].content_type == ContentType.FILM


async def test_one_users_interests_cannot_reorder_anothers_feed(db_session):
    """The isolation requirement, applied to recommendations."""
    heavy = await make_user(db_session, 9306)
    plain = await make_user(db_session, 9307)
    await _watch(db_session, heavy, ContentType.ANIME, 30)
    await _title(db_session, "Popular film", ContentType.FILM, views=9999)
    await _title(db_session, "Niche anime", ContentType.ANIME, views=1)

    # Resolve the heavy viewer first; the newcomer must be unaffected.
    assert (await _recommend(db_session, heavy))[0].content_type == ContentType.ANIME
    assert (await _recommend(db_session, plain))[0].name == "Popular film"
    # And the reverse order changes nothing.
    assert (await _recommend(db_session, heavy))[0].content_type == ContentType.ANIME


async def test_watched_titles_stay_excluded(db_session):
    """Prior behaviour preserved: the feed does not recommend what you saw."""
    user = await make_user(db_session, 9308)
    await _watch(db_session, user, ContentType.ANIME, 12)
    seen = {title.id for title in await _recommend(db_session, user)}
    watched = {
        row[0]
        for row in (
            await db_session.execute(
                __import__("sqlalchemy")
                .select(WatchHistory.title_id)
                .where(WatchHistory.user_id == user.id)
            )
        ).all()
    }
    assert not (seen & watched)
