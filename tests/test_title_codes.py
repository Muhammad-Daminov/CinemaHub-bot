"""
Public title codes: how they are allocated, and what must never happen.

A code is what a viewer types into the bot or the search box, so it is
also what ends up printed on a poster or pasted into a channel post. That
makes one property non-negotiable: **a code is never reused.** If a
deleted title's number is handed to the next one, every place the old
number was published silently starts pointing at the wrong film.

The first implementation derived codes from `MAX(code) + 1`, which is a
maximum over *surviving* rows — deleting the highest-coded title recycled
its number immediately. These tests exist because that shipped and was
caught only by trying it.
"""
import asyncio

import pytest
from sqlalchemy import select, text

from app.db.models.content import ContentType, Title
from app.services.admin_content import CODE_SEQUENCE, admin_content_service
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _create(session, name: str) -> Title:
    return await admin_content_service.create_title(
        session, name=name, content_type=ContentType.FILM
    )


async def test_a_new_title_is_given_a_code(db_session):
    title = await _create(db_session, "Coded")

    assert title.code is not None
    assert title.code.isdigit()


async def test_codes_are_distinct_and_ascending(db_session):
    first = await _create(db_session, "One")
    second = await _create(db_session, "Two")

    assert first.code != second.code
    assert int(second.code) > int(first.code)


async def test_a_deleted_titles_code_is_never_reused(db_session):
    """
    The regression. A published number must not start resolving to a
    different film because the original was removed.
    """
    doomed = await _create(db_session, "Doomed")
    retired = doomed.code

    await admin_content_service.delete_title(db_session, doomed.id)
    replacement = await _create(db_session, "Replacement")

    assert replacement.code != retired


async def test_deleting_the_highest_coded_title_does_not_rewind(db_session):
    """The specific shape that broke: deleting the *maximum*."""
    a = await _create(db_session, "A")
    b = await _create(db_session, "B")
    assert int(b.code) > int(a.code)

    await admin_content_service.delete_title(db_session, b.id)
    c = await _create(db_session, "C")

    assert int(c.code) > int(b.code)


async def test_a_hand_set_code_is_skipped_rather_than_colliding(db_session):
    """
    An administrator may set a memorable code by hand. The allocator must
    step over it instead of failing the next creation.
    """
    reserved = (
        await db_session.execute(select(text(f"nextval('{CODE_SEQUENCE}')")))
    ).scalar()

    squatter = await _create(db_session, "Squatter")
    squatter.code = str(int(reserved) + 1)
    await db_session.flush()

    following = await _create(db_session, "Following")

    assert following.code != squatter.code


async def test_codes_stay_unique_under_concurrent_creation(db_factory):
    """
    Two administrators adding titles at once. `nextval` is atomic; a
    read-then-increment would hand both the same number.
    """
    async def create(name: str) -> str:
        async with db_factory() as session:
            title = await _create(session, name)
            await session.commit()
            return title.code

    codes = await asyncio.gather(*(create(f"Concurrent {n}") for n in range(5)))

    assert len(set(codes)) == 5, codes


async def test_the_lookup_finds_a_title_by_its_code(db_session):
    from app.services.content import content_service

    title = await _create(db_session, "Findable")

    found = await content_service.by_code(db_session, title.code)
    assert found is not None and found.id == title.id


async def test_the_lookup_ignores_an_inactive_title(db_session):
    """An unpublished film must not be reachable by guessing its number."""
    from app.services.content import content_service

    title = await _create(db_session, "Hidden")
    title.is_active = False
    await db_session.flush()

    assert await content_service.by_code(db_session, title.code) is None


@pytest.mark.parametrize("value", ["", "   ", "nope", "1" * 17, None])
async def test_the_lookup_refuses_nonsense(db_session, value):
    from app.services.content import content_service

    assert await content_service.by_code(db_session, value) is None


async def test_a_code_is_compared_as_text_not_as_a_number(db_session):
    """
    "0042" and "42" are different codes, and neither is forty-two. Parsing
    would make a leading zero vanish and two codes collapse into one.
    """
    from app.services.content import content_service

    title = await _create(db_session, "Padded")
    title.code = "0042"
    await db_session.flush()

    assert (await content_service.by_code(db_session, "0042")) is not None
    assert (await content_service.by_code(db_session, "42")) is None
