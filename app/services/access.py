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


async def unlocks_premium(session: AsyncSession, user: User) -> bool:
    """
    Whether this viewer may open premium titles at all — the one question
    behind both the gate and the padlock the Mini App draws.

    Split out so a *listing* can answer "is this title locked for you?"
    without running the whole per-title check. `check_title_access` asks
    about one title and may call Telegram for the membership test; a home
    screen renders sixty cards and must not. The premium half needs
    neither the title nor the network — it is a property of the viewer —
    so asking it once per response is both correct and cheap.

    **A trial counts.** A trial is a real `chp_subscriptions` row with an
    expiry, and giving away a taste of premium is the entire point of
    offering one; `is_user_premium` therefore treats it exactly like a
    purchased plan. That is a deliberate product decision, recorded here
    because "does the free trial unlock paid films?" must never be an
    accident of which query happened to be reused. If it must ever change,
    change it here — `Subscription.plan_id IS NULL` distinguishes a trial
    from a purchase — and not by adding a second premium test elsewhere.

    Administrators pass: they have to be able to inspect what they are
    curating, and the alternative is an operator who cannot check whether
    the file they just uploaded plays.
    """
    if user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        return True
    return await is_user_premium(session, user.id)


async def unlocks_premium_by_id(session: AsyncSession, user_id: int | None) -> bool:
    """
    `unlocks_premium` for callers holding an internal user id.

    An adapter, not a second rule — it loads the row and asks the same
    function. The bot's card builders carry a `chp_users.id` (that is what
    the favourites lookup needs) while the rule takes the row itself.

    An id that resolves to nothing, or no id at all, is treated as no
    entitlement: someone who has never pressed /start has no subscription,
    so the locked card is the honest thing to show them. This decides only
    what a *card* looks like; delivery is gated separately and fails
    closed on the same unknown identity.
    """
    if user_id is None:
        return False
    viewer = await session.get(User, user_id)
    return await unlocks_premium(session, viewer) if viewer is not None else False


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
    # premium question and excuses the membership one. Shared with the
    # listing path so a card can never show unlocked while this refuses.
    premium = await unlocks_premium(session, user)
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
