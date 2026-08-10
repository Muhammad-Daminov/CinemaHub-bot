"""
The Telegram "/" command menu.

Registered once, on startup, from the list of commands that actually have
handlers. Before this the menu was never populated at all, so `/topup` —
the only way to add funds from the bot — was invisible: not on the reply
keyboard, not in `/help`, and not in the command menu a Telegram user
opens when they want to know what a bot can do.

Descriptions come from the locale catalogs and are registered per
language scope, so each user reads the menu in their own. `/start` is
listed because Telegram shows it regardless; listing it keeps the menu
honest rather than leaving one command described and another not.

Deliberately narrow: only commands with a real handler appear. A menu
entry for something that does nothing is worse than no menu, because the
user blames the bot rather than their memory.
"""
import logging

from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from app.bot.instance import bot
from app.core.i18n import t
from app.db.models.user import UILanguage

logger = logging.getLogger(__name__)

# (command, locale key for its description). Every entry here must have a
# handler — see app/bot/handlers/. `/promo` is a user command; the admin
# `/createpromo` is deliberately absent, since surfacing an admin-only
# command to all private chats invites failed attempts.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "command.start"),
    ("help", "command.help"),
    ("topup", "command.topup"),
    ("promo", "command.promo"),
)


async def register_bot_commands() -> int:
    """
    Publishes the command menu in every interface language.

    Returns how many scopes were set, so a caller (or a test) can tell the
    difference between "registered" and "quietly did nothing".
    """
    scopes = 0
    for language in UILanguage:
        await bot.set_my_commands(
            [
                BotCommand(command=command, description=t(key, language))
                for command, key in COMMANDS
            ],
            scope=BotCommandScopeAllPrivateChats(),
            language_code=language.value,
        )
        scopes += 1

    logger.info("Registered %d bot commands across %d language scopes", len(COMMANDS), scopes)
    return scopes
