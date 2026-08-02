"""
One-off migration: legacy content table (type IN 'kino', 'multifilm') -> chp_movies.

Read-only against the legacy `content` table. Idempotent via file_id lookup
against chp_movies. Run with: python scripts/migrate_legacy_movies.py
"""
import asyncio
import re

from sqlalchemy import select, text

from app.db.models.movie import Movie
from app.db.session import db_session_ctx


def parse_genres(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return re.findall(r"#(\S+)", raw)


async def main() -> None:
    inserted = 0
    skipped_no_name = 0
    skipped_existing = 0

    async with db_session_ctx() as session:
        result = await session.execute(
            text(
                "SELECT name, year, file_id, genre FROM content "
                "WHERE type IN ('kino', 'multifilm')"
            )
        )
        legacy_rows = result.mappings().all()

        for row in legacy_rows:
            if row["name"] is None:
                skipped_no_name += 1
                continue

            existing = await session.execute(
                select(Movie.id).where(Movie.file_id == row["file_id"])
            )
            if existing.scalar_one_or_none() is not None:
                skipped_existing += 1
                continue

            movie = Movie(
                title=row["name"],
                year=row["year"],
                file_id=row["file_id"],
                genres=parse_genres(row["genre"]),
                tmdb_id=None,
                poster_url=None,
                description=None,
                rating=None,
                is_manual_override=False,
            )
            session.add(movie)
            inserted += 1

    print("Legacy movie migration summary")
    print(f"  Inserted:                {inserted}")
    print(f"  Skipped (no name):       {skipped_no_name}")
    print(f"  Skipped (already exists): {skipped_existing}")


if __name__ == "__main__":
    asyncio.run(main())
