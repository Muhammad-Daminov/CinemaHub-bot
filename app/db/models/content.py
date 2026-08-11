"""
Content catalog — three levels.

    Title      one work: "Venom", "The Last of Us"
      └─ Episode   one watchable unit; a film has exactly one, a serial has many
           └─ MediaFile  one Telegram file_id in one language + quality

Why three levels instead of the old flat chp_movies:

- A film and a 20-episode serial are the same shape here (film = 1
  episode), so browsing, favourites and progress tracking all work
  against Episode without special-casing content_type everywhere.
- The same episode often exists in several languages (uz dub, ru dub).
  Those must appear as ONE card in the catalog, with delivery picking
  the file matching the user's language — that only works if language
  lives below the episode, not on the title.
- Quality is a label describing the uploaded file, not a transcoding
  target: we never re-encode, we just tell the user what they're
  getting.

chp_movies is intentionally left in place and untouched by this
module — the migration script copies out of it, and it can be dropped
manually once the new tables are verified.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Sequence,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
# The interface language is defined once, on the user model. Catalog
# translations key off the *same* enum so "the language a user picked" and
# "the language a title is stored in" can never drift into two vocabularies.
from app.db.models.user import UILanguage


class ContentType(str, enum.Enum):
    FILM = "film"
    SERIAL = "serial"
    MULTFILM = "multfilm"
    ANIME = "anime"
    DRAMA = "drama"


class AudioLanguage(str, enum.Enum):
    """Language of the audio/subtitles in a specific file — not the UI language."""

    UZ_DUB = "uz_dub"      # O'zbek dublyaj
    UZ_SUB = "uz_sub"      # O'zbek subtitr
    RU = "ru"              # Rus tilida
    EN = "en"              # Ingliz tilida
    ORIGINAL = "original"  # Original audio


class VideoQuality(str, enum.Enum):
    """Descriptive label for the uploaded file. We never transcode."""

    SD_480 = "480p"
    HD_720 = "720p"
    FHD_1080 = "1080p"
    UHD_4K = "4k"


# Owns public title numbering. Declared on the metadata as well as in
# migration f2b9c04e7a13 because the test schema is built by
# `metadata.create_all`: a sequence that existed only in the migration
# would be absent under test, and every "codes are never reused"
# assertion would pass against a database that could not honour it.
TITLE_CODE_SEQUENCE = Sequence("chp_title_code_seq", start=1000, metadata=Base.metadata)


class Title(Base):
    """One work — a film, serial, cartoon or anime."""

    __tablename__ = "chp_titles"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_type: Mapped[ContentType] = mapped_column(nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    year: Mapped[int | None] = mapped_column(Integer)
    genres: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    country: Mapped[str | None] = mapped_column(String(128))

    # TMDB-sourced metadata; nullable so manual entries work without a match.
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # TMDB's poster. Superseded by poster_image_id when an admin uploads
    # one — clearing the upload falls back here, which is why this is kept
    # rather than overwritten.
    poster_url: Mapped[str | None] = mapped_column(String(512))
    poster_image_id: Mapped[int | None] = mapped_column(ForeignKey("chp_uploaded_images.id"))
    description: Mapped[str | None] = mapped_column(String(2000))
    rating: Mapped[float | None] = mapped_column(Numeric(3, 1))

    view_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    is_manual_override: Mapped[bool] = mapped_column(default=False, nullable=False)

    # The short number a viewer types to find this title — in the bot chat
    # or the Mini App's search box. Deliberately NOT the primary key: an
    # id is an implementation detail whose sequence leaks how much catalog
    # exists and which rows were deleted, and it could never be reassigned
    # or printed on promotional material. Stored as text because a code is
    # an identifier, not a quantity: nothing arithmetic is ever done to it,
    # and a leading zero must survive.
    #
    # Nullable so a title can exist before it is given one, and unique so
    # one code can never resolve to two films.
    code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)

    # Whether watching this requires an active subscription. Channel
    # membership does not unlock it — that distinction is the whole point
    # of the flag, and it is enforced server-side in
    # app.services.access, never by hiding a button.
    is_premium: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="title", cascade="all, delete-orphan", order_by="Episode.season, Episode.number"
    )

    # lazy="raise": collections must be reached through an explicit join or
    # selectinload. A bare `title.collections` under async SQLAlchemy would
    # otherwise blow up with MissingGreenlet deep inside a request.
    collections: Mapped[list["Collection"]] = relationship(
        secondary="chp_title_collections", back_populates="titles", lazy="raise"
    )

    @property
    def is_single_episode(self) -> bool:
        """Films and one-off cartoons: the episode layer is an implementation detail we hide in the UI."""
        return self.content_type in (ContentType.FILM, ContentType.MULTFILM)


class Episode(Base):
    """
    One watchable unit. A film has exactly one episode (season=1,
    number=1) so that every piece of content has a uniform shape.
    """

    __tablename__ = "chp_episodes"
    __table_args__ = (UniqueConstraint("title_id", "season", "number", name="uq_episode_per_title"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("chp_titles.id"), nullable=False, index=True)

    season: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))  # optional episode title
    duration_minutes: Mapped[int | None] = mapped_column(Integer)

    view_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    title: Mapped["Title"] = relationship(back_populates="episodes")
    files: Mapped[list["MediaFile"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )


class MediaFile(Base):
    """
    One Telegram file for one episode, in one language and quality.
    Delivery picks among these based on the viewer's language setting.
    """

    __tablename__ = "chp_media_files"
    __table_args__ = (
        UniqueConstraint("episode_id", "language", "quality", name="uq_file_per_lang_quality"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("chp_episodes.id"), nullable=False, index=True)

    file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[AudioLanguage] = mapped_column(nullable=False, index=True)
    quality: Mapped[VideoQuality] = mapped_column(default=VideoQuality.HD_720, nullable=False)

    # Optional: where this file was originally posted, used as a copy_message
    # fallback if the file_id ever stops resolving.
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    source_message_id: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    episode: Mapped["Episode"] = relationship(back_populates="files")


class PendingUpload(Base):
    """
    A video an admin forwarded to the bot that isn't attached to any
    episode yet. The admin then completes the metadata from the Mini
    App, which is far easier than typing a file_id by hand on a phone.
    """
    __tablename__ = "chp_pending_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("chp_users.id"))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    source_message_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WatchHistory(Base):
    """
    One row per user per episode. Updated on re-watch rather than
    appended, so "continue watching" and the stats below stay cheap —
    a user with 500 views has 500 rows, not thousands.
    """
    __tablename__ = "chp_watch_history"
    __table_args__ = (UniqueConstraint("user_id", "episode_id", name="uq_watch_per_user_episode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("chp_users.id"), nullable=False, index=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("chp_titles.id"), nullable=False, index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("chp_episodes.id"), nullable=False, index=True)

    watch_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_watched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TranslationSource(str, enum.Enum):
    """
    Where a translation came from.

    Recorded because the two are not equal in authority: an administrator
    typed the manual one, and TMDB auto-fill must never overwrite it. It
    also makes "which of these did a person actually check?" answerable.
    """

    MANUAL = "manual"
    TMDB = "tmdb"


class TitleTranslation(Base):
    """
    One title's name (and description) in one interface language.

    A separate table rather than `name_ru` / `name_en` columns: adding a
    fourth language then costs a row, not a migration against production,
    and a title with no translation simply has no row instead of a column
    full of NULLs.

    `Title.name` stays authoritative and is the fallback. It is not a
    "default" in the abstract — this catalog is indexed in Uzbek
    ("Qum sayyorasi") while the TMDB record is English ("Dune"), which is
    why `apply_tmdb_match` deliberately never overwrites it. A row here
    for `uz` is therefore allowed but rarely needed; `ru` and `en` are the
    usual cases.
    """

    __tablename__ = "chp_title_translations"
    __table_args__ = (
        UniqueConstraint("title_id", "language", name="uq_title_translation_language"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(
        ForeignKey("chp_titles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[UILanguage] = mapped_column(nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Optional independently of the name: TMDB often has a localised title
    # with no localised overview, and half a translation is still useful.
    description: Mapped[str | None] = mapped_column(String(2000))

    source: Mapped[TranslationSource] = mapped_column(
        default=TranslationSource.MANUAL, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Favorite(Base):
    """A title a user saved for later. One row per user per title."""

    __tablename__ = "chp_favorites"
    __table_args__ = (UniqueConstraint("user_id", "title_id", name="uq_favorite_per_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("chp_users.id"), nullable=False, index=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("chp_titles.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Collection(Base):
    """
    A curated grouping of titles — "Marvel", "Yangi yil kinolari".

    Many-to-many: a title is usually in several at once (Marvel *and*
    Action), so this cannot live as a column on Title.
    """

    __tablename__ = "chp_collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    poster_url: Mapped[str | None] = mapped_column(String(512))

    # An admin-uploaded poster, overriding poster_url — the same pair
    # Title carries, and read through the same /api/movies/images route.
    #
    # The column has existed in the database since migration
    # f6b2d94ae713, which added it to chp_titles and chp_collections
    # together; only this model declaration was missed. Four call sites
    # already assumed it — the admin upload and clear routes, the admin
    # CollectionOut schema, and _collection_out in the viewer API — so
    # the model was the one place the column was absent, and the ORM
    # instance therefore never had the attribute at all. That is why
    # /collections and /search/all raised AttributeError rather than
    # merely rendering the wrong poster, and why the admin's uploaded
    # poster was silently discarded instead of saved.
    poster_image_id: Mapped[int | None] = mapped_column(ForeignKey("chp_uploaded_images.id"))

    # Hand-ordered rail position; ties fall back to name.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    titles: Mapped[list["Title"]] = relationship(
        secondary="chp_title_collections", back_populates="collections", lazy="raise"
    )


# Plain Table, not a mapped class: the link carries no data of its own, so a
# model would only add a surrogate id and an ORM cascade to reason about.
title_collections = Table(
    "chp_title_collections",
    Base.metadata,
    Column("title_id", ForeignKey("chp_titles.id"), primary_key=True, index=True),
    Column("collection_id", ForeignKey("chp_collections.id"), primary_key=True, index=True),
)
