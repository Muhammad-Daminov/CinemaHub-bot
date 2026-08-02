"""
One-off cleanup: collapse "<Name> N-fasl" titles into a single title
with proper season numbers.

The legacy `content` table stored each season as its own parent_name
("Tungi borilar 3-fasl"), so the first migration faithfully produced
one chp_titles row per season, all with season=1. This script merges
them: episodes move under the base-name title and get their real
season number from the suffix.

Idempotent: titles that no longer match the "N-fasl" pattern are left
alone, so re-running is a no-op.

Run:  python -m scripts.fix_seasons
"""
import asyncio
import re

from sqlalchemy import select

from app.db.models.content import Episode, Title
from app.db.session import db_session_ctx, engine

# "Tungi borilar 3-fasl" -> ("Tungi borilar", 3)
SEASON_PATTERN = re.compile(r"^(.*?)\s+(\d+)\s*-?\s*fasl\s*$", re.IGNORECASE)


def parse_season(name: str) -> tuple[str, int] | None:
    match = SEASON_PATTERN.match(name.strip())
    if not match:
        return None
    base_name, season = match.group(1).strip(), int(match.group(2))
    return (base_name, season) if base_name else None


async def fix_seasons() -> None:
    merged = 0
    seasons_set = 0
    titles_removed = 0

    async with db_session_ctx() as session:
        result = await session.execute(select(Title))
        all_titles = list(result.scalars())

        # Group the season-suffixed titles by their base name.
        groups: dict[str, list[tuple[Title, int]]] = {}
        for title in all_titles:
            parsed = parse_season(title.name)
            if parsed is None:
                continue
            base_name, season = parsed
            groups.setdefault(base_name.lower(), []).append((title, season))

        for base_key, members in groups.items():
            members.sort(key=lambda pair: pair[1])  # by season number
            base_name = parse_season(members[0][0].name)[0]

            # Reuse an existing base-name title if one exists (e.g. a
            # season-less "Breaking bad"), otherwise promote the lowest
            # season's title by renaming it — avoids creating a duplicate.
            base_lookup = await session.execute(select(Title).where(Title.name.ilike(base_name)))
            base_title = base_lookup.scalar_one_or_none()

            if base_title is None:
                base_title = members[0][0]
                base_title.name = base_name
                await session.flush()

            for title, season in members:
                episodes_result = await session.execute(
                    select(Episode).where(Episode.title_id == title.id)
                )
                episodes = list(episodes_result.scalars())

                for episode in episodes:
                    episode.season = season
                    if title.id != base_title.id:
                        episode.title_id = base_title.id
                    seasons_set += 1

                await session.flush()

                if title.id != base_title.id:
                    await session.delete(title)
                    titles_removed += 1

            merged += 1
            await session.flush()

    await engine.dispose()

    print("\n--- Season merge ---")
    print(f"series merged:   {merged}")
    print(f"episodes updated:{seasons_set}")
    print(f"titles removed:  {titles_removed}")


if __name__ == "__main__":
    asyncio.run(fix_seasons())
