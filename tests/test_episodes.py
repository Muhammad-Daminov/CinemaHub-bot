"""
Episode and season navigation (FR-9) and per-episode audio tracks (FR-7).

The ownership test is the important one: episode ids arrive from the
client, so `get_episode_of_title` is the boundary that stops one title's
id being used to fetch another title's content.
"""
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
from app.services.content import EPISODE_PAGE_SIZE, content_service
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _title(session, name="Test Serial", content_type=ContentType.SERIAL) -> Title:
    title = Title(content_type=content_type, name=name, is_active=True)
    session.add(title)
    await session.flush()
    return title


async def _episode(session, title: Title, season: int, number: int, **kwargs) -> Episode:
    episode = Episode(title_id=title.id, season=season, number=number, **kwargs)
    session.add(episode)
    await session.flush()
    return episode


async def _file(session, episode: Episode, language: AudioLanguage) -> MediaFile:
    media = MediaFile(
        episode_id=episode.id,
        file_id=f"file-{episode.id}-{language.value}",
        language=language,
        quality=VideoQuality.HD_720,
    )
    session.add(media)
    await session.flush()
    return media


# ---------- seasons ----------


async def test_seasons_are_listed_ascending(db_session):
    title = await _title(db_session)
    for season in (3, 1, 2):
        await _episode(db_session, title, season, 1)
    assert await content_service.list_seasons(db_session, title.id) == [1, 2, 3]


async def test_a_film_reports_a_single_season(db_session):
    """A film is a Title with one Episode, so the shape stays uniform."""
    title = await _title(db_session, "Test Film", ContentType.FILM)
    await _episode(db_session, title, 1, 1)
    assert await content_service.list_seasons(db_session, title.id) == [1]


# ---------- paging ----------


async def test_episode_page_orders_by_season_then_number(db_session):
    title = await _title(db_session)
    await _episode(db_session, title, 2, 1)
    await _episode(db_session, title, 1, 2)
    await _episode(db_session, title, 1, 1)

    episodes, has_more = await content_service.episode_page(db_session, title.id)
    assert [(e.season, e.number) for e in episodes] == [(1, 1), (1, 2), (2, 1)]
    assert has_more is False


async def test_episode_page_filters_by_season(db_session):
    title = await _title(db_session)
    await _episode(db_session, title, 1, 1)
    await _episode(db_session, title, 2, 1)
    await _episode(db_session, title, 2, 2)

    episodes, _ = await content_service.episode_page(db_session, title.id, season=2)
    assert [e.number for e in episodes] == [1, 2]
    assert all(e.season == 2 for e in episodes)


async def test_episode_page_reports_more_without_overfetching(db_session):
    """A long serial must not be loaded whole to render one screen."""
    title = await _title(db_session)
    for number in range(1, EPISODE_PAGE_SIZE + 6):
        await _episode(db_session, title, 1, number)

    first, has_more = await content_service.episode_page(db_session, title.id, page=0)
    assert len(first) == EPISODE_PAGE_SIZE
    assert has_more is True

    second, has_more_2 = await content_service.episode_page(db_session, title.id, page=1)
    assert len(second) == 5
    assert has_more_2 is False
    # Pages must not overlap, or the infinite-scroll list would show duplicates.
    assert not {e.id for e in first} & {e.id for e in second}


# ---------- ownership ----------


async def test_episode_of_another_title_is_not_returned(db_session):
    """
    The security boundary. Without it, passing any episode id to
    /watch would deliver content the request never asked for.
    """
    mine = await _title(db_session, "Mine")
    theirs = await _title(db_session, "Theirs")
    foreign = await _episode(db_session, theirs, 1, 1)

    assert await content_service.get_episode_of_title(db_session, mine.id, foreign.id) is None


async def test_own_episode_is_returned(db_session):
    title = await _title(db_session)
    episode = await _episode(db_session, title, 1, 1)
    found = await content_service.get_episode_of_title(db_session, title.id, episode.id)
    assert found is not None and found.id == episode.id


async def test_unknown_episode_id_is_not_returned(db_session):
    title = await _title(db_session)
    assert await content_service.get_episode_of_title(db_session, title.id, 999_999) is None


# ---------- first episode ----------


async def test_first_episode_is_the_lowest_season_and_number(db_session):
    title = await _title(db_session)
    await _episode(db_session, title, 2, 1)
    await _episode(db_session, title, 1, 5)
    expected = await _episode(db_session, title, 1, 1)

    found = await content_service.first_episode(db_session, title.id)
    assert found is not None and found.id == expected.id


async def test_first_episode_is_none_for_a_title_without_episodes(db_session):
    title = await _title(db_session)
    assert await content_service.first_episode(db_session, title.id) is None


# ---------- audio languages ----------


async def test_languages_are_grouped_per_episode(db_session):
    title = await _title(db_session)
    dubbed = await _episode(db_session, title, 1, 1)
    subtitled = await _episode(db_session, title, 1, 2)
    await _file(db_session, dubbed, AudioLanguage.UZ_DUB)
    await _file(db_session, dubbed, AudioLanguage.RU)
    await _file(db_session, subtitled, AudioLanguage.EN)

    grouped = await content_service.languages_by_episode(db_session, [dubbed.id, subtitled.id])
    assert set(grouped[dubbed.id]) == {AudioLanguage.UZ_DUB, AudioLanguage.RU}
    assert grouped[subtitled.id] == [AudioLanguage.EN]


async def test_episode_without_files_is_absent_from_the_grouping(db_session):
    """
    A partly-dubbed serial is the normal case; the UI shows "no file" for
    those rows rather than promising a track that isn't there.
    """
    title = await _title(db_session)
    empty = await _episode(db_session, title, 1, 1)
    grouped = await content_service.languages_by_episode(db_session, [empty.id])
    assert empty.id not in grouped


async def test_duplicate_languages_collapse(db_session):
    """Two qualities of the same dub are one audio option, not two."""
    title = await _title(db_session)
    episode = await _episode(db_session, title, 1, 1)
    await _file(db_session, episode, AudioLanguage.RU)
    media = MediaFile(
        episode_id=episode.id,
        file_id="second-ru",
        language=AudioLanguage.RU,
        quality=VideoQuality.FHD_1080,
    )
    db_session.add(media)
    await db_session.flush()

    grouped = await content_service.languages_by_episode(db_session, [episode.id])
    assert grouped[episode.id] == [AudioLanguage.RU]


async def test_empty_batch_makes_no_query(db_session):
    assert await content_service.languages_by_episode(db_session, []) == {}
    assert await content_service.watched_episode_ids(db_session, 1, []) == set()


# ---------- episode counts (the play-control guard) ----------


async def test_episode_counts_are_batched_per_title(db_session):
    """
    The client decides whether a play control may start playback directly
    or must open the selector. Getting this wrong is how a "Watch" button
    silently starts episode 1 on a serial.
    """
    film = await _title(db_session, "A Film", ContentType.FILM)
    await _episode(db_session, film, 1, 1)
    serial = await _title(db_session, "A Serial")
    for number in range(1, 4):
        await _episode(db_session, serial, 1, number)

    counts = await content_service.episode_counts(db_session, [film.id, serial.id])
    assert counts[film.id] == 1
    assert counts[serial.id] == 3


async def test_title_without_episodes_is_absent_from_counts(db_session):
    """Callers default to 1 for a missing entry, which keeps a bare title playable-looking."""
    empty = await _title(db_session)
    assert empty.id not in await content_service.episode_counts(db_session, [empty.id])


async def test_episode_counts_empty_batch_makes_no_query(db_session):
    assert await content_service.episode_counts(db_session, []) == {}


# ---------- watched state ----------


async def test_watched_episodes_are_reported(db_session):
    user = await make_user(db_session, 7001)
    title = await _title(db_session)
    seen = await _episode(db_session, title, 1, 1)
    unseen = await _episode(db_session, title, 1, 2)
    db_session.add(WatchHistory(user_id=user.id, title_id=title.id, episode_id=seen.id))
    await db_session.flush()

    watched = await content_service.watched_episode_ids(db_session, user.id, [seen.id, unseen.id])
    assert watched == {seen.id}


async def test_watched_state_is_per_user(db_session):
    viewer = await make_user(db_session, 7002)
    other = await make_user(db_session, 7003)
    title = await _title(db_session)
    episode = await _episode(db_session, title, 1, 1)
    db_session.add(WatchHistory(user_id=other.id, title_id=title.id, episode_id=episode.id))
    await db_session.flush()

    assert await content_service.watched_episode_ids(db_session, viewer.id, [episode.id]) == set()
