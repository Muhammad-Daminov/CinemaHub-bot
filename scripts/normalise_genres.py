"""
One-off: rewrite chp_titles.genres to canonical keys.

Idempotent. Canonical keys map to themselves in ALIASES, so a second run
finds nothing to change — safe to re-run after a later import, and safe
to run twice if the first attempt is interrupted.

Unmappable values are dropped, never guessed at. The summary prints them
explicitly so nothing disappears silently.

    python -m scripts.normalise_genres            # apply
    python -m scripts.normalise_genres --dry-run  # report only
"""
import argparse
import asyncio
from collections import Counter

from sqlalchemy import select, text

from app.core.genres import normalise_genre, normalise_genres
from app.db.models.content import Title
from app.db.session import AsyncSessionFactory

DISTINCT_SQL = text(
    "select g, count(*) from chp_titles, unnest(genres) g group by g order by count(*) desc, g"
)


async def main(dry_run: bool) -> None:
    async with AsyncSessionFactory() as session:
        before = (await session.execute(DISTINCT_SQL)).all()

        print(f"BEFORE — {len(before)} distinct values")
        for value, count in before:
            key = normalise_genre(value)
            arrow = f"-> {key}" if key else "-> DROPPED (no mapping)"
            print(f"  {value:<20} {count:>4}  {arrow}")

        # Only titles that actually have genres; NULL rows are left NULL.
        titles = list(
            (await session.execute(select(Title).where(Title.genres.is_not(None)))).scalars()
        )

        changed = 0
        dropped: Counter[str] = Counter()
        emptied = 0
        projected: Counter[str] = Counter()

        for title in titles:
            original = list(title.genres or [])
            canonical = normalise_genres(original)
            projected.update(canonical)

            for value in original:
                if normalise_genre(value) is None:
                    dropped[value] += 1

            if canonical == original:
                continue

            # Everything on this title was unmappable — store NULL rather
            # than an empty array, matching how untagged titles already look.
            title.genres = canonical or None
            if not canonical:
                emptied += 1
            changed += 1

        # Computed from the in-memory rewrite, so a dry run reports the same
        # table the real run will produce rather than only a change count.
        label = "AFTER (projected)" if dry_run else "AFTER"
        print(f"\n{label} — {len(projected)} distinct values")
        for value, count in sorted(projected.items(), key=lambda item: (-item[1], item[0])):
            print(f"  {value:<20} {count:>4}")

        print(f"\n{len(titles)} titles with genres · {changed} "
              f"{'would change' if dry_run else 'changed'}")
        if dropped:
            print("  dropped values: " + ", ".join(f"{v}×{n}" for v, n in dropped.most_common()))
        if emptied:
            print(f"  titles left with no genres at all: {emptied}")
        print(f"  reduction: {len(before)} -> {len(projected)} distinct values")

        if dry_run:
            await session.rollback()
            print("\nDRY RUN — nothing written.")
            return

        await session.commit()

        # Re-read from the database rather than trusting the in-memory view.
        verified = (await session.execute(DISTINCT_SQL)).all()
        unmapped = [v for v, _ in verified if normalise_genre(v) is None]
        print(f"\nVERIFIED FROM DB — {len(verified)} distinct values")
        for value, count in verified:
            print(f"  {value:<20} {count:>4}")
        print(f"\n  values still unmapped: {unmapped or 'none'}")
        print(f"  matches projection: {dict(verified) == dict(projected)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    asyncio.run(main(parser.parse_args().dry_run))
