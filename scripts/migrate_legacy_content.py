"""
One-off migration: legacy `content` table -> chp_titles / chp_episodes / chp_media_files.

Handles both shapes in the legacy data:

- Standalone rows (type in kino/multifilm): name is the work itself,
  becomes a Title with exactly one Episode.
- Part rows (type in part/drama): `parent_name` is the serial's name
  and `part_number` is the episode number. All parts sharing a
  parent_name collapse into ONE Title with many Episodes — which is
  the whole point of the new schema.

Read-only against `content`. Idempotent: every file_id already present
in chp_media_files is skipped, so re-running adds nothing.

Run:  python -m scripts.migrate_legacy_content
"""
import asyncio
import re
from collections import defaultdict

from sqlalchemy import select, text

from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Episode,
    MediaFile,
    Title,
    VideoQuality,
)
from app.db.session import db_session_ctx, engine

# Legacy `type` values -> new ContentType. Note both 'part' and 'drama'
# are serial episodes in this dataset (confirmed with the owner).
STANDALONE_TYPES = {"kino": ContentType.FILM, "multifilm": ContentType.MULTFILM}
SERIAL_TYPES = {"part": ContentType.SERIAL, "drama": ContentType.DRAMA}

LEGACY_LANG_MAP = {
    "o'zbek tilida": AudioLanguage.UZ_DUB,
    "uzbek tilida": AudioLanguage.UZ_DUB,
    "o‘zbek tilida": AudioLanguage.UZ_DUB,
    "rus tilida": AudioLanguage.RU,
    "ingliz tilida": AudioLanguage.EN,
}


def parse_genres(raw: str | None) -> list[str] | None:
    """Legacy format is a hashtag string: '#Komediya #Action' -> ['Komediya', 'Action']."""
    if not raw:
        return None
    found = re.findall(r"#(\w+)", raw)
    return found or None


def parse_language(raw: str | None) -> AudioLanguage:
    if not raw:
        return AudioLanguage.UZ_DUB  # dataset is overwhelmingly Uzbek dub
    return LEGACY_LANG_MAP.get(raw.strip().lower(), AudioLanguage.UZ_DUB)


async def fetch_legacy_rows(session) -> list[dict]:
    result = await session.execute(
        text(
            "SELECT id, type, name, year, genre, lang, country, file_id, part_number, parent_name "
            "FROM content ORDER BY id"
        )
    )
    return [dict(row._mapping) for row in result]


async def existing_file_ids(session) -> set[str]:
    result = await session.execute(select(MediaFile.file_id))
    return {row[0] for row in result}


async def migrate() -> None:
    stats = defaultdict(int)

    async with db_session_ctx() as session:
        rows = await fetch_legacy_rows(session)
        seen_file_ids = await existing_file_ids(session)

        # Cache of Titles created/reused in this run, keyed by (name, type).
        title_cache: dict[tuple[str, ContentType], Title] = {}

        async def get_or_create_title(name: str, content_type: ContentType, row: dict) -> Title:
            key = (name.strip().lower(), content_type)
            if key in title_cache:
                return title_cache[key]

            existing = await session.execute(
                select(Title).where(Title.name.ilike(name.strip()), Title.content_type == content_type)
            )
            title = existing.scalar_one_or_none()

            if title is None:
                title = Title(
                    name=name.strip(),
                    content_type=content_type,
                    year=row.get("year"),
                    genres=parse_genres(row.get("genre")),
                    country=row.get("country"),
                )
                session.add(title)
                await session.flush()
                stats["titles_created"] += 1

            title_cache[key] = title
            return title

        # --- pass 1: standalone films / cartoons ---
        for row in rows:
            legacy_type = (row["type"] or "").lower()
            if legacy_type not in STANDALONE_TYPES:
                continue
            if not row["name"]:
                stats["skipped_no_name"] += 1
                continue
            if row["file_id"] in seen_file_ids:
                stats["skipped_existing"] += 1
                continue

            title = await get_or_create_title(row["name"], STANDALONE_TYPES[legacy_type], row)

            episode = Episode(title_id=title.id, season=1, number=1)
            session.add(episode)
            await session.flush()

            session.add(
                MediaFile(
                    episode_id=episode.id,
                    file_id=row["file_id"],
                    language=parse_language(row.get("lang")),
                    quality=VideoQuality.HD_720,
                )
            )
            seen_file_ids.add(row["file_id"])
            stats["standalone_migrated"] += 1

        # --- pass 2: serial parts, grouped by parent_name ---
        for row in rows:
            legacy_type = (row["type"] or "").lower()
            if legacy_type not in SERIAL_TYPES:
                continue

            serial_name = row.get("parent_name") or row.get("name")
            if not serial_name:
                stats["skipped_no_parent_name"] += 1
                continue
            if row["file_id"] in seen_file_ids:
                stats["skipped_existing"] += 1
                continue

            title = await get_or_create_title(serial_name, SERIAL_TYPES[legacy_type], row)

            number = row.get("part_number") or 1
            existing_ep = await session.execute(
                select(Episode).where(
                    Episode.title_id == title.id, Episode.season == 1, Episode.number == number
                )
            )
            episode = existing_ep.scalar_one_or_none()
            if episode is None:
                episode = Episode(title_id=title.id, season=1, number=number)
                session.add(episode)
                await session.flush()

            session.add(
                MediaFile(
                    episode_id=episode.id,
                    file_id=row["file_id"],
                    language=parse_language(row.get("lang")),
                    quality=VideoQuality.HD_720,
                )
            )
            seen_file_ids.add(row["file_id"])
            stats["episodes_migrated"] += 1

    await engine.dispose()

    print("\n--- Legacy content migration ---")
    for key in sorted(stats):
        print(f"{key}: {stats[key]}")


if __name__ == "__main__":
    asyncio.run(migrate())
