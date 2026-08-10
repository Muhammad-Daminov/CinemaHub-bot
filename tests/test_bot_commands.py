"""
The Telegram "/" command menu.

The bot's only commands — `/topup`, the sole route to adding funds from
Telegram, and `/promo` — were undiscoverable three ways over: absent from
the reply keyboard, absent from `/help`, and absent from the command menu
Telegram shows when a user types "/". `set_my_commands` was never called
anywhere in the codebase.

The property that matters is that the menu and the handlers cannot drift:
a command advertised with no handler sends the user into silence, and a
handler with no menu entry stays secret.
"""
import json
from pathlib import Path

import pytest

from app.bot.commands import COMMANDS, register_bot_commands
from app.db.models.user import UILanguage

ROOT = Path(__file__).resolve().parent.parent


class FakeBot:
    """Records what would be published, so nothing reaches Telegram."""

    def __init__(self):
        self.calls: list[tuple[tuple[str, ...], str | None]] = []

    async def set_my_commands(self, commands, scope=None, language_code=None):
        self.calls.append((tuple(c.command for c in commands), language_code))


@pytest.fixture
def fake_bot(monkeypatch):
    from app.bot import commands as module

    bot = FakeBot()
    monkeypatch.setattr(module, "bot", bot)
    return bot


async def test_every_interface_language_gets_its_own_menu(fake_bot):
    scopes = await register_bot_commands()

    assert scopes == len(UILanguage)
    assert {language for _, language in fake_bot.calls} == {
        item.value for item in UILanguage
    }


async def test_the_menu_advertises_exactly_the_registered_commands(fake_bot):
    await register_bot_commands()

    for published, _language in fake_bot.calls:
        assert published == tuple(command for command, _ in COMMANDS)


async def test_topup_is_in_the_menu(fake_bot):
    """The regression this exists for: adding funds must be discoverable."""
    await register_bot_commands()

    published, _ = fake_bot.calls[0]
    assert "topup" in published


def test_every_advertised_command_has_a_handler():
    """
    A menu entry for a command nothing answers sends the user into
    silence. Checked against the handler sources rather than a second
    list, so the two cannot drift.
    """
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "app" / "bot" / "handlers").glob("*.py")
    )

    for command, _key in COMMANDS:
        if command == "start":
            assert "CommandStart()" in sources
        else:
            assert f'Command("{command}")' in sources, f"/{command} has no handler"


def test_no_admin_only_command_is_advertised():
    """
    `/createpromo` is admin-only. Offering it to every private chat would
    invite failed attempts and disclose that it exists.
    """
    assert "createpromo" not in [command for command, _ in COMMANDS]


def test_every_command_description_is_translated():
    for language in ("uz", "ru", "en"):
        catalog = json.loads(
            (ROOT / "app" / "locales" / f"{language}.json").read_text(encoding="utf-8")
        )
        for _command, key in COMMANDS:
            assert key in catalog, f"{key} missing from {language}"
            assert catalog[key].strip(), f"{key} is blank in {language}"


def test_help_mentions_the_commands_it_advertises():
    """
    The menu is one route to `/topup`; `/help` is the other. A help text
    that omits it leaves the reply-keyboard user with no way to find it.
    """
    for language in ("uz", "ru", "en"):
        catalog = json.loads(
            (ROOT / "app" / "locales" / f"{language}.json").read_text(encoding="utf-8")
        )
        assert "/topup" in catalog["help.text"]
        assert "/promo" in catalog["help.text"]
