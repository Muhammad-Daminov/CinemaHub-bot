"""
User provisioning — the path every /start and every Mini App request runs through.

Covers FR-8's two readings of "verify both new and existing users":
an existing user's Telegram profile must not go stale, and a user who
never answered the language question must still count as unanswered.
"""
import pytest

from app.db.models.user import UILanguage
from app.services.users import get_or_create_user, get_user_id
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


async def test_creates_a_user_on_first_contact(db_session):
    user = await get_or_create_user(db_session, 4242, "newbie", "New Bie")
    assert user.telegram_id == 4242
    assert user.username == "newbie"
    assert user.referral_code


async def test_new_user_has_not_selected_a_language(db_session):
    """
    The flag, not the row's age, is what /start gates the picker on — so a
    freshly created user must start out as 'has not answered'.
    """
    user = await get_or_create_user(db_session, 4243, "u", "U")
    assert user.language_selected is False
    assert user.language == UILanguage.UZ


async def test_returns_the_same_user_on_second_contact(db_session):
    first = await get_or_create_user(db_session, 4244, "u", "U")
    await db_session.commit()
    second = await get_or_create_user(db_session, 4244, "u", "U")
    assert first.id == second.id


async def test_username_change_is_picked_up(db_session):
    """
    Regression: profile fields were written once at signup and never
    again, so a user who changed their Telegram username kept the old one
    forever — in the admin list, the welcome message, and Mini App settings.
    """
    await get_or_create_user(db_session, 4245, "old_handle", "Old Name")
    await db_session.commit()

    refreshed = await get_or_create_user(db_session, 4245, "new_handle", "New Name")
    await db_session.commit()

    assert refreshed.username == "new_handle"
    assert refreshed.full_name == "New Name"


async def test_username_can_be_cleared(db_session):
    """Telegram usernames are optional and can be removed; the row must follow."""
    await get_or_create_user(db_session, 4246, "had_one", "Name")
    await db_session.commit()

    refreshed = await get_or_create_user(db_session, 4246, None, "Name")
    await db_session.commit()
    assert refreshed.username is None


async def test_absent_full_name_does_not_erase_a_known_one(db_session):
    """
    full_name is assembled from optional first/last name parts, so an
    update that carries neither must not blank out a good value.
    """
    await get_or_create_user(db_session, 4247, "u", "Real Name")
    await db_session.commit()

    refreshed = await get_or_create_user(db_session, 4247, "u", None)
    assert refreshed.full_name == "Real Name"


async def test_language_choice_survives_a_profile_refresh(db_session):
    """Refreshing Telegram fields must not disturb the language decision."""
    user = await get_or_create_user(db_session, 4248, "u", "U")
    user.language = UILanguage.RU
    user.language_selected = True
    await db_session.commit()

    refreshed = await get_or_create_user(db_session, 4248, "u2", "U2")
    assert refreshed.language == UILanguage.RU
    assert refreshed.language_selected is True


async def test_referral_is_captured_from_a_deep_link(db_session):
    referrer = await get_or_create_user(db_session, 4249, "ref", "Referrer")
    await db_session.commit()

    invited = await get_or_create_user(
        db_session, 4250, "invited", "Invited", f"REF_{referrer.referral_code}"
    )
    assert invited.referred_by_id == referrer.id


async def test_unknown_referral_code_is_ignored(db_session):
    invited = await get_or_create_user(db_session, 4251, "u", "U", "REF_NOSUCHCODE")
    assert invited.referred_by_id is None


async def test_get_user_id_returns_none_before_first_contact(db_session):
    assert await get_user_id(db_session, 999_888_777) is None


async def test_get_user_id_returns_the_row_id(db_session):
    user = await get_or_create_user(db_session, 4252, "u", "U")
    await db_session.commit()
    assert await get_user_id(db_session, 4252) == user.id
