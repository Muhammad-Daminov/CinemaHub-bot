"""
Who may watch what — decided in exactly one place.

The counterpart to `permissions.has_permission` (may this admin act?) and
`plan_features.features_for_user` (what did they buy?). This one answers
"may this person watch this title?", and both surfaces ask it: the bot's
handlers and the Mini App's API. A second copy of these rules would
eventually disagree with the first, and the direction it would disagree
in is giving away paid content.

Two gates, in a deliberate order:

**A subscription outranks channel membership.** Someone paying is not
also asked to join a channel — the channel requirement exists to grow an
audience from people who are not paying, and charging someone and then
still gating them behind a join is the kind of thing that produces
refund requests. So premium is checked first and, when present, ends the
question.

**Premium titles are not unlocked by membership.** That is the entire
meaning of the flag. A channel member with no subscription can watch
everything marked free and nothing marked premium.

When a subscription lapses the user simply falls back through the same
two gates: free titles return to needing membership, premium titles
become unavailable until they subscribe again. Nothing needs to be
recalculated or swept — expiry is read from the row every time.
"""
import enum
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import Title
from app.db.models.user import User, UserRole
# Imported as a module, not by name: the membership lookup is the seam the
# rest of the suite patches (`membership.is_channel_member`), and binding
# the function here at import time would silently bypass every one of
# those patches — and, worse, any future one.
from app.services import membership as membership_module
from app.services.membership import MembershipConfig
from app.services.settings_store import get_membership_config
from app.services.subscriptions import is_user_premium


class AccessDecision(str, enum.Enum):
    """Why a viewer may or may not watch. Each maps to one message."""

    ALLOWED = "allowed"
    # Free title, membership required, user is not a member.
    NEEDS_MEMBERSHIP = "needs_membership"
    # Premium title, no active subscription. Joining the channel will not help.
    NEEDS_PREMIUM = "needs_premium"


@dataclass(frozen=True)
class AccessResult:
    decision: AccessDecision
    # Carried so the caller can build the join prompt without reading the
    # settings a second time.
    membership: MembershipConfig

    @property
    def allowed(self) -> bool:
        return self.decision is AccessDecision.ALLOWED


async def check_title_access(
    session: AsyncSession, bot: Bot, user: User, title: Title
) -> AccessResult:
    """
    Whether `user` may watch `title`.

    Administrators pass unconditionally — they have to be able to inspect
    what they are curating, and the alternative is an operator who cannot
    check whether the file they just uploaded plays.
    """
    config = await get_membership_config(session)

    if user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        return AccessResult(AccessDecision.ALLOWED, config)

    # Asked once and reused for both gates: a subscription answers the
    # premium question and excuses the membership one.
    premium = await is_user_premium(session, user.id)
    if premium:
        return AccessResult(AccessDecision.ALLOWED, config)

    if title.is_premium:
        # Deliberately does not fall through to the membership check.
        # Joining a channel must never unlock paid content.
        return AccessResult(AccessDecision.NEEDS_PREMIUM, config)

    if not config.active:
        return AccessResult(AccessDecision.ALLOWED, config)

    if await membership_module.is_channel_member(bot, config.channel, user.telegram_id):
        return AccessResult(AccessDecision.ALLOWED, config)
    return AccessResult(AccessDecision.NEEDS_MEMBERSHIP, config)


def access_message_key(decision: AccessDecision) -> str:
    """The locale key explaining a refusal. One mapping, both surfaces."""
    return {
        AccessDecision.NEEDS_MEMBERSHIP: "membership.required",
        AccessDecision.NEEDS_PREMIUM: "access.premium_required",
    }[decision]
