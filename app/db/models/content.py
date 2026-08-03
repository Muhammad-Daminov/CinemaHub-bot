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
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


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
    poster_url: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(String(2000))
    rating: Mapped[float | None] = mapped_column(Numeric(3, 1))

    view_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    is_manual_override: Mapped[bool] = mapped_column(default=False, nullable=False)

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
