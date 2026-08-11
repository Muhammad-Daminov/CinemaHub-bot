"""
Per-language catalog titles (FR-6 requirement 3).

Three properties carry the feature, and each has a way of failing that
looks like something else:

  **Fallback.** A title with no translation must read as its stored name,
  not as blank. A half-filled translation must fall back field by field,
  or a Russian name with no Russian overview would blank the description.

  **Search.** Titles are indexed in Uzbek and known abroad by their
  English names. Searching must cross languages, and a title with three
  translations must still appear once.

  **Authority.** TMDB auto-fill must never overwrite what an
  administrator typed — that is the whole reason `source` is stored.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Episode,
    MediaFile,
    Title,
    TitleTranslation,
    TranslationSource,
    VideoQuality,
)
from app.db.models.user import UILanguage
from app.db.session import get_db_session
from app.main import app
from app.services.admin_content import admin_content_service
from app.services.content import content_service
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _title(session, name: str, description: str | None = "Asl tavsif") -> Title:
    """A playable title — the catalog gate hides one with no files."""
    title = Title(
        content_type=ContentType.FILM, name=name, description=description, is_active=True
    )
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


async def _translate(session, title, language, name, description=None, source=TranslationSource.MANUAL):
    row = TitleTranslation(
        title_id=title.id,
        language=language,
        name=name,
        description=description,
        source=source,
    )
    session.add(row)
    await session.flush()
    return row


# ---------- resolution and fallback ----------


async def test_a_translated_title_reads_in_the_viewers_language(db_session):
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.RU, "Дюна", "Описание")

    localized = await content_service.localized_title(db_session, title, UILanguage.RU)
    assert localized.name == "Дюна"
    assert localized.description == "Описание"


async def test_an_untranslated_title_falls_back_to_the_stored_name(db_session):
    """The stored name is the Uzbek one this catalog is indexed by — never blank."""
    title = await _title(db_session, "Qum sayyorasi")

    for language in UILanguage:
        localized = await content_service.localized_title(db_session, title, language)
        assert localized.name == "Qum sayyorasi"


async def test_fallback_is_per_field_not_per_row(db_session):
    """
    TMDB frequently has a localised title and no localised overview.
    Dropping the original description in that case would lose text the
    viewer could otherwise read.
    """
    title = await _title(db_session, "Qum sayyorasi", description="Asl tavsif")
    await _translate(db_session, title, UILanguage.EN, "Dune", description=None)

    localized = await content_service.localized_title(db_session, title, UILanguage.EN)
    assert localized.name == "Dune"
    assert localized.description == "Asl tavsif"


async def test_one_language_does_not_leak_into_another(db_session):
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.RU, "Дюна")

    assert (await content_service.localized_title(db_session, title, UILanguage.EN)).name == (
        "Qum sayyorasi"
    )


async def test_a_page_of_titles_is_resolved_in_one_query(db_session):
    """Every title is present in the result, translated or not — callers never guess."""
    first = await _title(db_session, "Birinchi")
    second = await _title(db_session, "Ikkinchi")
    await _translate(db_session, first, UILanguage.RU, "Первый")

    resolved = await content_service.localized_titles(
        db_session, [first, second], UILanguage.RU
    )
    assert resolved[first.id].name == "Первый"
    assert resolved[second.id].name == "Ikkinchi"


async def test_resolving_an_empty_page_is_harmless(db_session):
    assert await content_service.localized_titles(db_session, [], UILanguage.RU) == {}


# ---------- search across languages ----------


async def test_search_finds_a_title_by_its_translated_name(db_session):
    """A Russian speaker types "Дюна"; the row is stored as "Qum sayyorasi"."""
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.RU, "Дюна")

    page = await content_service.browse(db_session, query="Дюна")
    assert [t.id for t in page.titles] == [title.id]


async def test_search_still_finds_a_title_by_its_stored_name(db_session):
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.RU, "Дюна")

    page = await content_service.browse(db_session, query="sayyorasi")
    assert [t.id for t in page.titles] == [title.id]


async def test_a_title_with_several_translations_appears_once(db_session):
    """An EXISTS, not a join — a join would return the title once per translation."""
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.RU, "Дюна Дюна")
    await _translate(db_session, title, UILanguage.EN, "Dune Дюна")

    page = await content_service.browse(db_session, query="Дюна")
    assert len(page.titles) == 1


async def test_search_is_not_limited_to_one_language(db_session):
    """
    Searching only the viewer's own language would hide a title they know
    by its English name — the usual case for this audience.
    """
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.EN, "Dune")

    assert [t.id for t in (await content_service.browse(db_session, query="Dune")).titles] == [
        title.id
    ]


# ---------- admin write path ----------


async def test_setting_a_translation_stores_it(db_session):
    title = await _title(db_session, "Qum sayyorasi")
    rows = await admin_content_service.set_title_translations(
        db_session, title.id, {UILanguage.RU: ("Дюна", "Описание")}
    )
    assert [(r.language, r.name) for r in rows] == [(UILanguage.RU, "Дюна")]
    assert rows[0].source == TranslationSource.MANUAL


async def test_editing_a_translation_updates_rather_than_duplicates(db_session):
    title = await _title(db_session, "Qum sayyorasi")
    await admin_content_service.set_title_translations(
        db_session, title.id, {UILanguage.RU: ("Дюна", None)}
    )
    await admin_content_service.set_title_translations(
        db_session, title.id, {UILanguage.RU: ("Дюна: Часть вторая", None)}
    )

    assert await count_rows(db_session, TitleTranslation, title_id=title.id) == 1
    localized = await content_service.localized_title(db_session, title, UILanguage.RU)
    assert localized.name == "Дюна: Часть вторая"


async def test_an_empty_name_removes_the_translation(db_session):
    """The only way a plain text field can express "delete this"."""
    title = await _title(db_session, "Qum sayyorasi")
    await admin_content_service.set_title_translations(
        db_session, title.id, {UILanguage.RU: ("Дюна", None)}
    )
    await admin_content_service.set_title_translations(
        db_session, title.id, {UILanguage.RU: ("   ", None)}
    )

    assert await count_rows(db_session, TitleTranslation, title_id=title.id) == 0
    assert (
        await content_service.localized_title(db_session, title, UILanguage.RU)
    ).name == "Qum sayyorasi", "removing a translation must restore the fallback"


async def test_editing_one_language_leaves_the_others_alone(db_session):
    title = await _title(db_session, "Qum sayyorasi")
    await admin_content_service.set_title_translations(
        db_session,
        title.id,
        {UILanguage.RU: ("Дюна", None), UILanguage.EN: ("Dune", None)},
    )
    await admin_content_service.set_title_translations(
        db_session, title.id, {UILanguage.RU: ("Дюна 2", None)}
    )

    rows = {r.language: r.name for r in await admin_content_service.list_title_translations(db_session, title.id)}
    assert rows == {UILanguage.RU: "Дюна 2", UILanguage.EN: "Dune"}


async def test_deleting_a_title_removes_its_translations(db_session):
    """A translation outliving its title is a row nothing can reach or remove."""
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.RU, "Дюна")

    await admin_content_service.delete_title(db_session, title.id)
    assert await count_rows(db_session, TitleTranslation, title_id=title.id) == 0


# ---------- TMDB auto-fill ----------


class FakeTMDB:
    """Returns a distinct localised title per locale, like TMDB does."""

    def __init__(self):
        self.calls: list[str] = []

    async def get_movie_details(self, tmdb_id: int, language: str | None = None):
        self.calls.append(language or "default")
        return {
            "id": tmdb_id,
            "title": {"ru-RU": "Дюна", "en-US": "Dune"}.get(language, "Dune"),
            "overview": {"ru-RU": "Описание", "en-US": "Overview"}.get(language, "Overview"),
        }


@pytest.fixture
def fake_tmdb(monkeypatch):
    from app.services import admin_content as module

    fake = FakeTMDB()
    monkeypatch.setattr(module, "tmdb_service", fake)
    return fake


async def test_tmdb_fill_stores_russian_and_english(db_session, fake_tmdb):
    title = await _title(db_session, "Qum sayyorasi")
    title.tmdb_id = 438631
    await db_session.flush()

    rows = await admin_content_service.fill_translations_from_tmdb(db_session, title)
    stored = {r.language: r.name for r in rows}

    assert stored == {UILanguage.RU: "Дюна", UILanguage.EN: "Dune"}
    assert all(r.source == TranslationSource.TMDB for r in rows)


async def test_tmdb_fill_does_not_attempt_uzbek(db_session, fake_tmdb):
    """TMDB holds essentially no Uzbek metadata, and Title.name already is it."""
    title = await _title(db_session, "Qum sayyorasi")
    title.tmdb_id = 1
    await db_session.flush()

    await admin_content_service.fill_translations_from_tmdb(db_session, title)
    assert "uz-UZ" not in fake_tmdb.calls
    assert await count_rows(db_session, TitleTranslation, language=UILanguage.UZ) == 0


async def test_tmdb_fill_never_overwrites_a_manual_translation(db_session, fake_tmdb):
    """The reason `source` is stored at all."""
    title = await _title(db_session, "Qum sayyorasi")
    title.tmdb_id = 1
    await db_session.flush()
    await admin_content_service.set_title_translations(
        db_session, title.id, {UILanguage.RU: ("Дюна (tuzatilgan)", None)}
    )

    await admin_content_service.fill_translations_from_tmdb(db_session, title)

    localized = await content_service.localized_title(db_session, title, UILanguage.RU)
    assert localized.name == "Дюна (tuzatilgan)"


async def test_tmdb_fill_may_be_re_run(db_session, fake_tmdb):
    title = await _title(db_session, "Qum sayyorasi")
    title.tmdb_id = 1
    await db_session.flush()

    await admin_content_service.fill_translations_from_tmdb(db_session, title)
    await admin_content_service.fill_translations_from_tmdb(db_session, title)

    assert await count_rows(db_session, TitleTranslation, title_id=title.id) == 2


async def test_a_title_without_a_tmdb_id_is_skipped(db_session, fake_tmdb):
    title = await _title(db_session, "Mahalliy kino")
    assert await admin_content_service.fill_translations_from_tmdb(db_session, title) == []
    assert fake_tmdb.calls == []


async def test_a_tmdb_failure_does_not_break_the_title(db_session, monkeypatch):
    """Enrichment is best-effort; a title with no translations still works."""
    from app.services import admin_content as module

    class BrokenTMDB:
        async def get_movie_details(self, *args, **kwargs):
            raise RuntimeError("TMDB is down")

    monkeypatch.setattr(module, "tmdb_service", BrokenTMDB())
    title = await _title(db_session, "Qum sayyorasi")
    title.tmdb_id = 1
    await db_session.flush()

    assert await admin_content_service.fill_translations_from_tmdb(db_session, title) == []


# ---------- the API serves the viewer's language ----------


@pytest.fixture
def as_user(db_session):
    def _install(user):
        async def override_session():
            yield db_session

        async def override_user():
            return user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _install
    app.dependency_overrides.clear()


async def test_the_catalog_returns_the_translated_name(db_session, as_user):
    user = await make_user(db_session, 9401)
    user.language = UILanguage.RU
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.RU, "Дюна", "Описание")
    await db_session.commit()

    async with as_user(user) as client:
        listed = (await client.get("/api/movies")).json()
        card = next(item for item in listed if item["id"] == title.id)
        assert card["title"] == "Дюна"
        assert card["description"] == "Описание"


async def test_the_same_row_reads_differently_for_two_users(db_session, as_user):
    """The point of the feature, asserted end to end."""
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.EN, "Dune")
    russian = await make_user(db_session, 9402)
    russian.language = UILanguage.RU
    english = await make_user(db_session, 9403)
    english.language = UILanguage.EN
    await db_session.commit()

    async with as_user(english) as client:
        assert (await client.get(f"/api/movies/{title.id}")).json()["title"] == "Dune"
    async with as_user(russian) as client:
        assert (await client.get(f"/api/movies/{title.id}")).json()["title"] == "Qum sayyorasi"


async def test_the_search_endpoint_matches_translations(db_session, as_user):
    user = await make_user(db_session, 9404)
    title = await _title(db_session, "Qum sayyorasi")
    await _translate(db_session, title, UILanguage.EN, "Dune")
    await db_session.commit()

    async with as_user(user) as client:
        results = (await client.get("/api/movies/search", params={"q": "Dune"})).json()
        assert [item["id"] for item in results] == [title.id]


async def test_the_response_shape_exposes_no_translation_internals(db_session, as_user):
    """
    Resolution is server-side: the client never learns a title has more
    than one name, so no frontend change was needed for any of this.

    The card is allowed to grow — `code`, `is_premium` and `is_locked`
    were added deliberately when premium titles shipped — but it must
    never grow a *translation* field. `title` and `description` stay
    single, already-resolved strings; the day a `translations` list or a
    `name_ru` appears here, the resolution has leaked to the client and
    the two surfaces can start disagreeing about what a film is called.

    So this asserts both halves: the exact set (a new field is a decision,
    not an accident) and, separately, that nothing translation-shaped is
    in it — which is the part that must hold whatever else is added.
    """
    user = await make_user(db_session, 9405)
    title = await _title(db_session, "Qum sayyorasi")
    await db_session.commit()

    async with as_user(user) as client:
        card = (await client.get(f"/api/movies/{title.id}")).json()

    assert set(card) == {
        "id",
        "title",
        "year",
        "genres",
        "poster_url",
        "description",
        "rating",
        "view_count",
        "episode_count",
        "is_favorite",
        "code",
        "is_premium",
        "is_locked",
    }
    assert isinstance(card["title"], str)
    assert not any(
        "translation" in key or key.endswith(("_uz", "_ru", "_en")) for key in card
    )
