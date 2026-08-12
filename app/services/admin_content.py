"""
Admin-side writes for the Title/Episode/MediaFile catalog.

Deliberately separate from app.services.content: that module owns the
viewer-facing read/delivery paths, this one owns the mutating admin
operations behind /api/admin. Keeping the write methods out of the
service the public routes use means a bug in a catalog route can never
reach them.

Every query is explicit — no lazy relationship access anywhere, since
these run under async SQLAlchemy where a lazy load raises
MissingGreenlet. Deletes use Core `delete()` in child-first order
rather than relying on ORM cascade, which would need the relationship
loaded to work.
"""
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.genres import normalise_genres
from app.db.models.content import (
    AudioLanguage,
    Collection,
    ContentType,
    Episode,
    Favorite,
    MediaFile,
    PendingUpload,
    TITLE_CODE_SEQUENCE,
    Title,
    TitleTranslation,
    TranslationSource,
    VideoQuality,
    WatchHistory,
    title_collections,
)
from app.db.models.payment import PaymentReceipt, PaymentStatus
from app.db.models.promo import PromoCode
from app.db.models.user import Subscription, UILanguage, User
from app.services.content import _title_name_matches
from app.services.tmdb import tmdb_service

logger = logging.getLogger(__name__)

ACTIVITY_DAYS = 7

# Our interface languages mapped onto TMDB locales. Uzbek is absent on
# purpose: TMDB holds essentially no Uzbek metadata, and Title.name is
# already the Uzbek name the catalog is indexed by.
TMDB_LOCALES: dict[UILanguage, str] = {
    UILanguage.RU: "ru-RU",
    UILanguage.EN: "en-US",
}

# Fields enrich_from_tmdb would overwrite. Editing one of these is what
# earns a title its is_manual_override flag — housekeeping edits like
# is_active or country must leave it enrichable.
TMDB_MANAGED_FIELDS = {"name", "year", "genres", "poster_url", "description", "rating"}


@dataclass
class DashboardStats:
    total_users: int
    premium_users: int
    total_titles: int
    total_episodes: int
    titles_by_type: dict[str, int] = field(default_factory=dict)
    pending_receipts: int = 0
    pending_uploads: int = 0
    total_revenue: float = 0.0
    active_promo_codes: int = 0


# Re-exported from the model, which is where the sequence is declared, so
# the runtime, the migration and the test schema all name one object.
CODE_SEQUENCE = TITLE_CODE_SEQUENCE.name

# Bounded retry for the hand-set-code collision described in _next_code.
_CODE_ATTEMPTS = 50


class AdminContentService:
    """Catalog mutations + dashboard aggregates for the admin dashboard."""

    # ---------- titles ----------

    async def create_title(
        self,
        session: AsyncSession,
        name: str,
        content_type: ContentType,
        year: int | None = None,
        genres: list[str] | None = None,
        country: str | None = None,
        description: str | None = None,
        poster_url: str | None = None,
        tmdb_id: int | None = None,
        rating: float | None = None,
        is_premium: bool = False,
    ) -> Title:
        title = Title(
            name=name.strip(),
            content_type=content_type,
            year=year,
            # `or None` keeps an absent genre list as SQL NULL rather than
            # introducing a second empty-ish value alongside the existing NULLs.
            genres=normalise_genres(genres) or None,
            country=country,
            description=description,
            poster_url=poster_url,
            tmdb_id=tmdb_id,
            rating=rating,
            is_premium=is_premium,
        )
        session.add(title)
        await session.flush()

        # A title with no code is unreachable by the one search a viewer is
        # most likely to be given — a number on a poster or in a channel
        # post. Assigned on creation so the catalog never grows rows that
        # the code lookup cannot see; the migration did the same for every
        # title that already existed.
        title.code = await self._next_code(session)
        await session.flush()
        return title

    async def _next_code(self, session: AsyncSession) -> str:
        """
        The next free public code, from the dedicated sequence.

        A sequence rather than `MAX(code) + 1`: a maximum taken over
        surviving rows hands a deleted title's number to the next one, and
        that number may already be printed on a poster or sitting in a
        channel post. `nextval` is a high-water mark — it does not roll
        back on delete, and it is atomic, so two administrators creating
        titles at once cannot be handed the same number.

        The loop covers the one case the sequence cannot know about: a
        code set by hand. It advances past a collision rather than failing
        the creation, and is bounded so a pathological catalog cannot spin
        here forever — the unique index is still the backstop.
        """
        for _ in range(_CODE_ATTEMPTS):
            candidate = str((await session.execute(TITLE_CODE_SEQUENCE.next_value().select())).scalar())
            taken = (
                await session.execute(select(Title.id).where(Title.code == candidate))
            ).scalars().first()
            if taken is None:
                return candidate

        raise RuntimeError("Could not allocate a free title code")

    async def update_title(self, session: AsyncSession, title_id: int, **fields) -> Title | None:
        """
        Admin hand-edit. Flips is_manual_override only when the edit touches
        a field TMDB would otherwise overwrite (TMDB_MANAGED_FIELDS), so
        toggling is_active or fixing a country doesn't permanently freeze
        the title out of enrichment.
        """
        title = await session.get(Title, title_id)
        if title is None:
            return None

        for key, value in fields.items():
            if not hasattr(title, key):
                raise ValueError(f"Title has no field '{key}'")
            # Hand-typed genres go through the same funnel as TMDB's, so an
            # admin typing "Jangari" cannot reopen the split either.
            setattr(title, key, (normalise_genres(value) or None) if key == "genres" else value)

        if "is_manual_override" not in fields and TMDB_MANAGED_FIELDS & fields.keys():
            title.is_manual_override = True

        await session.flush()
        # The admin just told us exactly which TMDB record this is, so its
        # localised names are as trustworthy as they will ever be.
        await self.fill_translations_from_tmdb(session, title)
        return title

    async def set_title_active(self, session: AsyncSession, title_id: int, is_active: bool) -> Title | None:
        title = await session.get(Title, title_id)
        if title is None:
            return None
        title.is_active = is_active
        await session.flush()
        return title

    async def delete_title(self, session: AsyncSession, title_id: int) -> bool:
        """Removes the title with its episodes and their files. Returns False if it never existed."""
        exists = (await session.execute(select(Title.id).where(Title.id == title_id))).scalar_one_or_none()
        if exists is None:
            return False

        # Viewer-owned rows first. These were missed, and the omission is
        # why deleting a title silently did nothing: chp_watch_history
        # points at both the title *and* its episodes, and neither foreign
        # key cascades, so as soon as one person had watched or saved the
        # film the episode delete below raised ForeignKeyViolationError.
        # The request 500'd, the admin panel swallowed it, and the title
        # was still there after the list refreshed.
        #
        # A title nobody had touched deleted perfectly, which is exactly
        # why this survived: it only fails for films that have an audience.
        await session.execute(delete(WatchHistory).where(WatchHistory.title_id == title_id))
        await session.execute(delete(Favorite).where(Favorite.title_id == title_id))
        # The association carries no data of its own, so the rows go with
        # the title rather than the collections, which outlive it.
        await session.execute(
            delete(title_collections).where(title_collections.c.title_id == title_id)
        )

        episode_ids = list(
            (await session.execute(select(Episode.id).where(Episode.title_id == title_id))).scalars()
        )
        if episode_ids:
            await session.execute(delete(MediaFile).where(MediaFile.episode_id.in_(episode_ids)))
            await session.execute(delete(Episode).where(Episode.id.in_(episode_ids)))
        # Explicit, though the foreign key also cascades: this module's rule
        # is child-first Core deletes, and relying on the database alone
        # would leave the ORM's create_all-built test schema as the only
        # place the behaviour is proven.
        await session.execute(delete(TitleTranslation).where(TitleTranslation.title_id == title_id))
        await session.execute(delete(Title).where(Title.id == title_id))
        await session.flush()
        return True

    async def list_titles(
        self,
        session: AsyncSession,
        query: str | None = None,
        content_type: ContentType | None = None,
        is_active: bool | None = None,
        is_premium: bool | None = None,
        page: int = 0,
        page_size: int = 20,
    ) -> tuple[list[tuple[Title, int, int]], int]:
        """
        Returns ((title, episode_count, file_count) rows, total_matching).

        The counts come from correlated scalar subqueries rather than
        relationship access — one round trip, and nothing lazy-loads.
        """
        episode_count = (
            select(func.count(Episode.id)).where(Episode.title_id == Title.id).scalar_subquery()
        )
        file_count = (
            select(func.count(MediaFile.id))
            .join(Episode, Episode.id == MediaFile.episode_id)
            .where(Episode.title_id == Title.id)
            .scalar_subquery()
        )

        filters = []
        if query:
            # Same predicate the catalog search uses, so an admin looking
            # for "Дюна" finds the row a Russian-speaking viewer would.
            filters.append(_title_name_matches(query))
        if content_type is not None:
            filters.append(Title.content_type == content_type)
        if is_active is not None:
            filters.append(Title.is_active.is_(is_active))
        # Filtered in SQL, not in the client: this list is paged, so
        # dropping rows after the fact would leave `total` and the page
        # count describing a different set than the one on screen — and
        # would get slower every time the catalog grows. `is_premium` is
        # indexed, so this narrows the same scan rather than adding work.
        if is_premium is not None:
            filters.append(Title.is_premium.is_(is_premium))

        total = (
            await session.execute(select(func.count(Title.id)).where(*filters))
        ).scalar_one()

        result = await session.execute(
            select(Title, episode_count, file_count)
            .where(*filters)
            .order_by(Title.created_at.desc(), Title.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
        return [(row[0], row[1], row[2]) for row in result.all()], total

    # ---------- episodes ----------

    async def add_episode(
        self,
        session: AsyncSession,
        title_id: int,
        season: int,
        number: int,
        name: str | None = None,
        duration_minutes: int | None = None,
    ) -> Episode:
        episode = Episode(
            title_id=title_id,
            season=season,
            number=number,
            name=name,
            duration_minutes=duration_minutes,
        )
        session.add(episode)
        try:
            await session.flush()
        except IntegrityError as exc:
            # uq_episode_per_title fired. Without this the admin gets a bare
            # 500 for what is really "you already added that one".
            raise HTTPException(
                status_code=409,
                detail=f"Season {season}, episode {number} already exists for this title.",
            ) from exc
        return episode

    async def get_or_create_episode(
        self, session: AsyncSession, title_id: int, season: int, number: int
    ) -> Episode:
        """Used by the pending-upload attach flow, which must not fail on an existing episode."""
        result = await session.execute(
            select(Episode).where(
                Episode.title_id == title_id, Episode.season == season, Episode.number == number
            )
        )
        episode = result.scalar_one_or_none()
        if episode is not None:
            return episode
        return await self.add_episode(session, title_id, season, number)

    async def delete_episode(self, session: AsyncSession, episode_id: int) -> bool:
        exists = (
            await session.execute(select(Episode.id).where(Episode.id == episode_id))
        ).scalar_one_or_none()
        if exists is None:
            return False
        await session.execute(delete(MediaFile).where(MediaFile.episode_id == episode_id))
        await session.execute(delete(Episode).where(Episode.id == episode_id))
        await session.flush()
        return True

    async def list_episodes_with_counts(
        self, session: AsyncSession, title_id: int
    ) -> list[tuple[Episode, int]]:
        file_count = (
            select(func.count(MediaFile.id))
            .where(MediaFile.episode_id == Episode.id)
            .scalar_subquery()
        )
        result = await session.execute(
            select(Episode, file_count)
            .where(Episode.title_id == title_id)
            .order_by(Episode.season, Episode.number)
        )
        return [(row[0], row[1]) for row in result.all()]

    # ---------- media files ----------

    async def attach_file(
        self,
        session: AsyncSession,
        episode_id: int,
        file_id: str,
        language: AudioLanguage,
        quality: VideoQuality,
        source_chat_id: int | None = None,
        source_message_id: int | None = None,
    ) -> MediaFile:
        """
        Adds a file to an episode. (episode, language, quality) is unique in
        the schema, so a repeat upload for the same slot replaces the
        file_id instead of raising an IntegrityError at the admin.
        """
        result = await session.execute(
            select(MediaFile).where(
                MediaFile.episode_id == episode_id,
                MediaFile.language == language,
                MediaFile.quality == quality,
            )
        )
        media_file = result.scalar_one_or_none()

        if media_file is not None:
            media_file.file_id = file_id
            if source_chat_id is not None:
                media_file.source_chat_id = source_chat_id
            if source_message_id is not None:
                media_file.source_message_id = source_message_id
        else:
            media_file = MediaFile(
                episode_id=episode_id,
                file_id=file_id,
                language=language,
                quality=quality,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
            session.add(media_file)

        await session.flush()
        return media_file

    async def detach_file(self, session: AsyncSession, file_id_pk: int) -> bool:
        exists = (
            await session.execute(select(MediaFile.id).where(MediaFile.id == file_id_pk))
        ).scalar_one_or_none()
        if exists is None:
            return False
        await session.execute(delete(MediaFile).where(MediaFile.id == file_id_pk))
        await session.flush()
        return True

    async def list_files(self, session: AsyncSession, episode_id: int) -> list[MediaFile]:
        result = await session.execute(
            select(MediaFile).where(MediaFile.episode_id == episode_id).order_by(MediaFile.language)
        )
        return list(result.scalars())

    # ---------- catalog translations ----------

    async def list_title_translations(
        self, session: AsyncSession, title_id: int
    ) -> list[TitleTranslation]:
        result = await session.execute(
            select(TitleTranslation)
            .where(TitleTranslation.title_id == title_id)
            .order_by(TitleTranslation.language)
        )
        return list(result.scalars())

    async def set_title_translations(
        self,
        session: AsyncSession,
        title_id: int,
        entries: dict[UILanguage, tuple[str | None, str | None]],
    ) -> list[TitleTranslation]:
        """
        Replaces the translations named in `entries` — `{language: (name, description)}`.

        A blank name **deletes** that language's row rather than storing an
        empty string: an empty translation would win the fallback and blank
        the title, and "clear this translation" has to be expressible from
        a form whose only affordance is emptying the field.

        Languages absent from `entries` are untouched, so editing Russian
        cannot silently drop English.

        Upserted rather than delete-then-insert so `created_at` survives an
        edit, and so two administrators saving at once cannot collide on
        the unique constraint — the second becomes an update.
        """
        for language, (name, description) in entries.items():
            cleaned = (name or "").strip()
            if not cleaned:
                await session.execute(
                    delete(TitleTranslation).where(
                        TitleTranslation.title_id == title_id,
                        TitleTranslation.language == language,
                    )
                )
                continue

            await session.execute(
                pg_insert(TitleTranslation)
                .values(
                    title_id=title_id,
                    language=language,
                    name=cleaned,
                    description=(description or "").strip() or None,
                    source=TranslationSource.MANUAL,
                )
                .on_conflict_do_update(
                    constraint="uq_title_translation_language",
                    set_={
                        "name": cleaned,
                        "description": (description or "").strip() or None,
                        # An edit through this path is a person's decision,
                        # so it is promoted to MANUAL and TMDB auto-fill
                        # will no longer touch it.
                        "source": TranslationSource.MANUAL.value,
                        "updated_at": func.now(),
                    },
                )
            )

        await session.flush()
        return await self.list_title_translations(session, title_id)

    async def fill_translations_from_tmdb(
        self, session: AsyncSession, title: Title
    ) -> list[TitleTranslation]:
        """
        Fills Russian and English from TMDB, which already holds both.

        Free coverage: the client is here, the id is on the row, and TMDB
        returns a localised `title` and `overview` for any locale it knows,
        falling back to the original where it does not. Uzbek is not
        attempted — TMDB has essentially no Uzbek metadata, and `Title.name`
        is already the Uzbek name this catalog is indexed by.

        **A manual translation is never overwritten.** An administrator who
        corrected a name must not have it undone by the next enrichment,
        which is the whole reason `source` exists.

        A TMDB failure is not an error: enrichment is best-effort and a
        title without translations still works.
        """
        if title.tmdb_id is None:
            return []

        manual = {
            row.language
            for row in await self.list_title_translations(session, title.id)
            if row.source == TranslationSource.MANUAL
        }

        for language, locale in TMDB_LOCALES.items():
            if language in manual:
                continue
            try:
                details = await tmdb_service.get_movie_details(title.tmdb_id, language=locale)
            except Exception:  # noqa: BLE001 — enrichment is best-effort
                logger.warning("TMDB %s lookup failed for title %s", locale, title.id)
                continue

            name = (details.get("title") or "").strip()
            if not name:
                continue

            await session.execute(
                pg_insert(TitleTranslation)
                .values(
                    title_id=title.id,
                    language=language,
                    name=name,
                    description=(details.get("overview") or "").strip() or None,
                    source=TranslationSource.TMDB,
                )
                .on_conflict_do_update(
                    constraint="uq_title_translation_language",
                    set_={
                        "name": name,
                        "description": (details.get("overview") or "").strip() or None,
                        "source": TranslationSource.TMDB.value,
                        "updated_at": func.now(),
                    },
                )
            )

        await session.flush()
        return await self.list_title_translations(session, title.id)

    # ---------- TMDB enrichment ----------

    async def enrich_from_tmdb(self, session: AsyncSession, title_id: int) -> Title | None:
        """
        Fills TMDB metadata by searching on name+year. No match is not an
        error — plenty of this catalog is local content TMDB has never
        heard of, so we leave the row alone and report it unchanged.
        Manually-overridden rows are never touched.
        """
        title = await session.get(Title, title_id)
        if title is None or title.is_manual_override:
            return title

        results = await tmdb_service.search_movie(title.name, year=title.year)
        if not results:
            return title

        details = await tmdb_service.get_movie_details(results[0]["id"])
        release_date = details.get("release_date") or ""

        title.tmdb_id = details.get("id")
        title.poster_url = tmdb_service.build_poster_url(details.get("poster_path"))
        title.description = details.get("overview")
        title.rating = details.get("vote_average")
        # Canonical keys on write. Storing TMDB's English labels raw is what
        # created the two-vocabulary split in the first place.
        genres = normalise_genres([g["name"] for g in details.get("genres", [])])
        if genres:
            title.genres = genres
        if title.year is None and release_date[:4].isdigit():
            title.year = int(release_date[:4])

        await session.flush()
        # Free coverage while we are already talking to TMDB about this row.
        await self.fill_translations_from_tmdb(session, title)
        return title

    async def search_tmdb(self, query: str, limit: int = 10) -> list[dict]:
        """
        Raw TMDB search, flattened for the admin picker. Read-only — nothing
        is written until the admin picks a specific result.
        """
        results = await tmdb_service.search_movie(query.strip())
        flattened = []
        for item in results[:limit]:
            release_date = item.get("release_date") or ""
            flattened.append(
                {
                    "id": item["id"],
                    "title": item.get("title") or item.get("original_title") or "",
                    "original_title": item.get("original_title"),
                    "year": int(release_date[:4]) if release_date[:4].isdigit() else None,
                    "poster_url": tmdb_service.build_poster_url(item.get("poster_path")),
                    "overview": item.get("overview") or None,
                }
            )
        return flattened

    async def apply_tmdb_match(
        self, session: AsyncSession, title_id: int, tmdb_id: int
    ) -> Title | None:
        """
        Apply one hand-picked TMDB entry to a title.

        Title.name is deliberately NOT touched: this catalog is indexed in
        Uzbek ("Qum sayyorasi") and that is what users search for, while the
        TMDB record is English ("Dune"). Overwriting the name would make the
        title unfindable for the people it exists for.

        Sets is_manual_override so a later auto-enrich — which searches by
        the Uzbek name and would find nothing or the wrong film — cannot
        undo the admin's choice.
        """
        title = await session.get(Title, title_id)
        if title is None:
            return None

        details = await tmdb_service.get_movie_details(tmdb_id)

        title.tmdb_id = details.get("id", tmdb_id)
        title.poster_url = tmdb_service.build_poster_url(details.get("poster_path"))
        title.description = details.get("overview")
        title.rating = details.get("vote_average")
        # Canonical keys on write. Storing TMDB's English labels raw is what
        # created the two-vocabulary split in the first place.
        genres = normalise_genres([g["name"] for g in details.get("genres", [])])
        if genres:
            title.genres = genres
        title.is_manual_override = True

        await session.flush()
        return title

    # ---------- dashboard ----------

    async def dashboard_stats(self, session: AsyncSession) -> DashboardStats:
        now = datetime.now(timezone.utc)

        total_users = (await session.execute(select(func.count(User.id)))).scalar_one()
        premium_users = (
            await session.execute(
                select(func.count(func.distinct(Subscription.user_id))).where(
                    Subscription.expires_at > now
                )
            )
        ).scalar_one()
        total_titles = (await session.execute(select(func.count(Title.id)))).scalar_one()
        total_episodes = (await session.execute(select(func.count(Episode.id)))).scalar_one()

        by_type_result = await session.execute(
            select(Title.content_type, func.count(Title.id)).group_by(Title.content_type)
        )
        titles_by_type = {row[0].value: row[1] for row in by_type_result.all()}

        pending_receipts = (
            await session.execute(
                select(func.count(PaymentReceipt.id)).where(
                    PaymentReceipt.status == PaymentStatus.PENDING
                )
            )
        ).scalar_one()
        pending_uploads = (
            await session.execute(select(func.count(PendingUpload.id)))
        ).scalar_one()
        total_revenue = (
            await session.execute(
                select(func.coalesce(func.sum(PaymentReceipt.amount), 0)).where(
                    PaymentReceipt.status == PaymentStatus.APPROVED
                )
            )
        ).scalar_one()
        active_promo_codes = (
            await session.execute(select(func.count(PromoCode.id)).where(PromoCode.is_active.is_(True)))
        ).scalar_one()

        return DashboardStats(
            total_users=total_users,
            premium_users=premium_users,
            total_titles=total_titles,
            total_episodes=total_episodes,
            titles_by_type=titles_by_type,
            pending_receipts=pending_receipts,
            pending_uploads=pending_uploads,
            total_revenue=float(total_revenue),
            active_promo_codes=active_promo_codes,
        )

    async def activity_last_7_days(self, session: AsyncSession) -> list[dict]:
        """New users per day. Days with no signups are filled in as 0 so the chart has no gaps."""
        today = datetime.now(timezone.utc).date()
        since = today - timedelta(days=ACTIVITY_DAYS - 1)

        day_column = func.date(User.created_at).label("day")
        result = await session.execute(
            select(day_column, func.count(User.id))
            .where(func.date(User.created_at) >= since)
            .group_by(day_column)
            .order_by(day_column)
        )
        counts: dict[date, int] = {row[0]: row[1] for row in result.all()}

        return [
            {"date": since + timedelta(days=offset), "count": counts.get(since + timedelta(days=offset), 0)}
            for offset in range(ACTIVITY_DAYS)
        ]

    async def premium_user_ids(self, session: AsyncSession, user_ids: Sequence[int]) -> set[int]:
        """
        Which of these users hold a live subscription — one query for a whole
        page of the user list, rather than a per-row premium check.
        """
        if not user_ids:
            return set()
        result = await session.execute(
            select(Subscription.user_id)
            .where(
                Subscription.user_id.in_(user_ids),
                Subscription.expires_at > datetime.now(timezone.utc),
            )
            .distinct()
        )
        return set(result.scalars())

    async def top_users(self, session: AsyncSession, limit: int = 5) -> list[dict]:
        result = await session.execute(
            select(User.telegram_id, User.username, User.balance)
            .order_by(User.balance.desc())
            .limit(limit)
        )
        return [
            {"telegram_id": row[0], "username": row[1], "balance": float(row[2])}
            for row in result.all()
        ]

    # ---------- collections ----------

    @staticmethod
    def slugify(name: str) -> str:
        """ASCII-ish slug; falls back to a name hash if nothing survives."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
        return slug or f"collection-{abs(hash(name)) % 10**6}"

    async def create_collection(
        self,
        session: AsyncSession,
        name: str,
        description: str | None = None,
        poster_url: str | None = None,
        sort_order: int = 0,
        slug: str | None = None,
    ) -> Collection:
        collection = Collection(
            name=name.strip(),
            slug=(slug or self.slugify(name)),
            description=description,
            poster_url=poster_url,
            sort_order=sort_order,
        )
        session.add(collection)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"A collection named '{name.strip()}' already exists."
            ) from exc
        return collection

    async def update_collection(
        self, session: AsyncSession, collection_id: int, **fields
    ) -> Collection | None:
        collection = await session.get(Collection, collection_id)
        if collection is None:
            return None
        for key, value in fields.items():
            if not hasattr(collection, key):
                raise ValueError(f"Collection has no field '{key}'")
            setattr(collection, key, value)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="Another collection already uses that name or slug."
            ) from exc
        return collection

    async def set_collection_active(
        self, session: AsyncSession, collection_id: int, is_active: bool
    ) -> Collection | None:
        collection = await session.get(Collection, collection_id)
        if collection is None:
            return None
        collection.is_active = is_active
        await session.flush()
        return collection

    async def delete_collection(self, session: AsyncSession, collection_id: int) -> bool:
        """Drops the collection and its links. Titles themselves are untouched."""
        exists = (
            await session.execute(select(Collection.id).where(Collection.id == collection_id))
        ).scalar_one_or_none()
        if exists is None:
            return False
        await session.execute(
            delete(title_collections).where(title_collections.c.collection_id == collection_id)
        )
        await session.execute(delete(Collection).where(Collection.id == collection_id))
        await session.flush()
        return True

    async def list_collections_admin(
        self, session: AsyncSession
    ) -> list[tuple[Collection, int]]:
        """All collections (active or not) with their raw title counts."""
        title_count = (
            select(func.count(title_collections.c.title_id))
            .where(title_collections.c.collection_id == Collection.id)
            .scalar_subquery()
        )
        result = await session.execute(
            select(Collection, title_count).order_by(Collection.sort_order, Collection.name)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def add_title_to_collection(
        self, session: AsyncSession, collection_id: int, title_id: int
    ) -> None:
        """Idempotent — re-adding an already-linked title is a no-op, not a 409."""
        statement = (
            pg_insert(title_collections)
            .values(title_id=title_id, collection_id=collection_id)
            .on_conflict_do_nothing()
        )
        await session.execute(statement)
        await session.flush()

    async def remove_title_from_collection(
        self, session: AsyncSession, collection_id: int, title_id: int
    ) -> None:
        await session.execute(
            delete(title_collections).where(
                title_collections.c.collection_id == collection_id,
                title_collections.c.title_id == title_id,
            )
        )
        await session.flush()

    async def collection_titles(
        self, session: AsyncSession, collection_id: int
    ) -> list[Title]:
        result = await session.execute(
            select(Title)
            .join(title_collections, title_collections.c.title_id == Title.id)
            .where(title_collections.c.collection_id == collection_id)
            .order_by(Title.name)
        )
        return list(result.scalars())

    async def title_collection_ids(self, session: AsyncSession, title_id: int) -> list[int]:
        result = await session.execute(
            select(title_collections.c.collection_id).where(
                title_collections.c.title_id == title_id
            )
        )
        return [row[0] for row in result.all()]

    async def set_title_collections(
        self, session: AsyncSession, title_id: int, collection_ids: list[int]
    ) -> list[int]:
        """Replaces a title's collection membership wholesale."""
        await session.execute(
            delete(title_collections).where(title_collections.c.title_id == title_id)
        )
        if collection_ids:
            await session.execute(
                pg_insert(title_collections)
                .values([{"title_id": title_id, "collection_id": cid} for cid in set(collection_ids)])
                .on_conflict_do_nothing()
            )
        await session.flush()
        return await self.title_collection_ids(session, title_id)

    # ---------- duplicate detection ----------

    async def similar_titles(
        self, session: AsyncSession, name: str, limit: int = 5
    ) -> list[tuple[Title, int, list[str]]]:
        """
        Fuzzy name matches, with episode count and the audio languages
        already attached.

        The language list is the whole point: before adding "O'rgimchak
        odam" for the third time, the admin needs to see that the row
        already exists AND whether the Russian dub is already on it.

        Matches both directions — the stored name containing the typed
        text, or the typed text containing the stored name — so "Venom"
        finds "Venom 3" and "Venom 3 (2021)" finds "Venom 3".
        """
        needle = name.strip()
        if len(needle) < 2:
            return []

        episode_count = (
            select(func.count(Episode.id)).where(Episode.title_id == Title.id).scalar_subquery()
        )
        result = await session.execute(
            select(Title, episode_count)
            .where(
                or_(
                    Title.name.ilike(f"%{needle}%"),
                    literal(needle).ilike(func.concat("%", Title.name, "%")),
                )
            )
            .order_by(Title.name)
            .limit(limit)
        )
        rows = [(row[0], row[1]) for row in result.all()]
        if not rows:
            return []

        # One grouped query for every match's languages, not one per row.
        title_ids = [title.id for title, _ in rows]
        language_result = await session.execute(
            select(Episode.title_id, MediaFile.language)
            .join(MediaFile, MediaFile.episode_id == Episode.id)
            .where(Episode.title_id.in_(title_ids))
            .group_by(Episode.title_id, MediaFile.language)
        )
        by_title: dict[int, list[str]] = {}
        for title_id, language in language_result.all():
            by_title.setdefault(title_id, []).append(language.value)

        return [(title, count, sorted(by_title.get(title.id, []))) for title, count in rows]

    # ---------- pending uploads ----------

    async def list_pending_uploads(self, session: AsyncSession, limit: int = 100) -> list[PendingUpload]:
        result = await session.execute(
            select(PendingUpload).order_by(PendingUpload.created_at.desc()).limit(limit)
        )
        return list(result.scalars())

    async def delete_pending_upload(self, session: AsyncSession, pending_id: int) -> bool:
        exists = (
            await session.execute(select(PendingUpload.id).where(PendingUpload.id == pending_id))
        ).scalar_one_or_none()
        if exists is None:
            return False
        await session.execute(delete(PendingUpload).where(PendingUpload.id == pending_id))
        await session.flush()
        return True


admin_content_service = AdminContentService()
