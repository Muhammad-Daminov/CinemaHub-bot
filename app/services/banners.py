"""
Resolving hero banners for one viewer, and validating what admins write.

**Resolution is per request, per user, and never cached globally.** The
active-campaign set is small (a handful of rows), so the saving from a
shared cache would be negligible while the risk is exactly the failure
the brief forbids: one user's personalized selection reaching another.
The only caching involved is Phase 9B's interest profile, which is keyed
by user id in its own table.

**Ordering is total and deterministic.** Personalized matches come first,
then priority, then newest, then id. A carousel whose slide order changed
between two requests would read as a bug.

**Admin input is validated, not trusted.** Text is plain text — angle
brackets are refused outright rather than escaped, because a banner has
no legitimate use for them and refusing is easier to verify than
sanitising. Image URLs must be http(s) or one of our own image routes, so
a campaign cannot carry a `javascript:` payload.
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.banner import Banner, BannerAudience
from app.db.models.content import Title
from app.db.models.user import User
from app.services.personalization import get_profile
from app.services.subscriptions import is_user_premium

logger = logging.getLogger(__name__)

MAX_BANNERS = 6

# The only locale keys a campaign may name. An allowlist rather than free
# text: the label is rendered in the viewer's language, and letting an
# admin type a key would either miss the catalog or invite injection.
ALLOWED_LABEL_KEYS = frozenset(
    {
        "banner.label.coming_soon",
        "banner.label.new",
        "banner.label.trending",
        "banner.label.exclusive",
        "banner.label.last_chance",
    }
)

# Anything that could start markup or a script URL. Refused, not escaped —
# a banner headline has no legitimate use for these.
_FORBIDDEN_TEXT = re.compile(r"[<>]")
_ALLOWED_IMAGE = re.compile(r"^(https://|http://|/api/movies/images/\d+$)")


class BannerError(Exception):
    """Raised when a campaign's configuration is not acceptable."""


@dataclass(frozen=True)
class ResolvedBanner:
    """One slide, already decided for this viewer."""

    id: int
    title_id: int | None
    headline: str | None
    subtitle: str | None
    label_key: str | None
    image_url: str | None
    audience: BannerAudience
    # True when this slide was chosen for *this* user's interests rather
    # than shown to everyone. Surfaced so the ordering is explainable.
    personalized: bool


def validate_text(value: str | None, *, field: str, limit: int) -> str | None:
    """Plain text only, trimmed, length-bounded."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise BannerError(f"{field} is longer than {limit} characters")
    if _FORBIDDEN_TEXT.search(cleaned):
        raise BannerError(f"{field} may not contain < or >")
    return cleaned


def validate_label_key(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    if value not in ALLOWED_LABEL_KEYS:
        raise BannerError("Unknown label")
    return value


def validate_image_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip()
    if not _ALLOWED_IMAGE.match(cleaned):
        raise BannerError("Image must be an http(s) URL or an uploaded image path")
    if len(cleaned) > 512:
        raise BannerError("Image URL is too long")
    return cleaned


def validate_audience(audience: BannerAudience, target_value: str | None) -> str | None:
    """
    A targeted campaign must say what it targets; an untargeted one must not.

    Storing a stray target on a GLOBAL banner would be harmless today and
    misleading the moment someone reads the row to work out who sees it.
    """
    needs_target = audience in (BannerAudience.CONTENT_TYPE, BannerAudience.BADGE)
    cleaned = (target_value or "").strip() or None

    if needs_target and not cleaned:
        raise BannerError(f"{audience.value} targeting needs a target value")
    if not needs_target:
        return None
    if len(cleaned) > 64 or _FORBIDDEN_TEXT.search(cleaned):
        raise BannerError("Invalid target value")
    return cleaned


def _is_live(banner: Banner, now: datetime) -> bool:
    """Inside its window. Both bounds are optional; absent means unbounded."""
    if banner.starts_at is not None and banner.starts_at > now:
        return False
    if banner.ends_at is not None and banner.ends_at <= now:
        return False
    return True


def _matches(banner: Banner, dominant_type: str | None, badge_key: str | None, premium: bool) -> bool:
    """Whether this campaign is for this viewer."""
    if banner.audience == BannerAudience.GLOBAL:
        return True
    if banner.audience == BannerAudience.PREMIUM:
        return premium
    if banner.audience == BannerAudience.FREE:
        return not premium
    if banner.audience == BannerAudience.CONTENT_TYPE:
        return dominant_type is not None and banner.target_value == dominant_type
    if banner.audience == BannerAudience.BADGE:
        # Prefix match, so "badge.anime." targets every anime tier rather
        # than forcing a campaign per tier.
        return badge_key is not None and banner.target_value is not None and badge_key.startswith(
            banner.target_value
        )
    return False


async def resolve_for_user(
    session: AsyncSession, user: User, limit: int = MAX_BANNERS
) -> list[ResolvedBanner]:
    """
    The slides this viewer should see, most relevant first.

    Reads the caller's own interest profile through Phase 9B — this module
    does not reimplement any of that logic. Nothing here is memoised across
    users; the candidate set is a handful of rows and correctness is worth
    far more than the query it would save.
    """
    now = datetime.now(timezone.utc)

    candidates = (
        await session.execute(
            select(Banner)
            .where(
                Banner.is_active.is_(True),
                or_(Banner.starts_at.is_(None), Banner.starts_at <= now),
                or_(Banner.ends_at.is_(None), Banner.ends_at > now),
            )
            # Total order: priority, then newest, then id. Without the last
            # tiebreak two banners created in the same transaction could
            # swap places between requests.
            .order_by(Banner.priority.desc(), Banner.created_at.desc(), Banner.id.desc())
        )
    ).scalars().all()

    if not candidates:
        return []

    profile = await get_profile(session, user.id)
    premium = await is_user_premium(session, user.id)

    resolved: list[ResolvedBanner] = []
    for banner in candidates:
        if not _is_live(banner, now) or not _matches(
            banner, profile.dominant_type, profile.badge_key, premium
        ):
            continue
        resolved.append(
            ResolvedBanner(
                id=banner.id,
                title_id=banner.title_id,
                headline=banner.headline,
                subtitle=banner.subtitle,
                label_key=banner.label_key,
                image_url=banner.image_url,
                audience=banner.audience,
                personalized=banner.audience != BannerAudience.GLOBAL,
            )
        )

    # Personalized slides lead. `sorted` is stable, so the priority order
    # established above survives inside each group.
    resolved.sort(key=lambda item: not item.personalized)
    return resolved[:limit]


async def list_banners(session: AsyncSession) -> list[Banner]:
    """Every campaign, live or not — the admin screen shows what it manages."""
    result = await session.execute(
        select(Banner).order_by(Banner.priority.desc(), Banner.created_at.desc(), Banner.id.desc())
    )
    return list(result.scalars())


async def create_banner(session: AsyncSession, **fields) -> Banner:
    banner = Banner(**await _clean(session, fields))
    session.add(banner)
    await session.flush()
    return banner


async def update_banner(session: AsyncSession, banner_id: int, **fields) -> Banner | None:
    banner = await session.get(Banner, banner_id)
    if banner is None:
        return None
    for key, value in (await _clean(session, fields, partial=True)).items():
        setattr(banner, key, value)
    await session.flush()
    return banner


async def _clean(session: AsyncSession, fields: dict, partial: bool = False) -> dict:
    """
    Validates and normalises admin input.

    On a partial update only the supplied fields are touched, so editing a
    headline cannot silently blank a schedule.
    """
    cleaned: dict = {}

    if "headline" in fields or not partial:
        cleaned["headline"] = validate_text(fields.get("headline"), field="Headline", limit=120)
    if "subtitle" in fields or not partial:
        cleaned["subtitle"] = validate_text(fields.get("subtitle"), field="Subtitle", limit=200)
    if "label_key" in fields or not partial:
        cleaned["label_key"] = validate_label_key(fields.get("label_key"))
    if "image_url" in fields or not partial:
        cleaned["image_url"] = validate_image_url(fields.get("image_url"))

    if "audience" in fields or not partial:
        audience = fields.get("audience") or BannerAudience.GLOBAL
        cleaned["audience"] = audience
        cleaned["target_value"] = validate_audience(audience, fields.get("target_value"))

    if "title_id" in fields or not partial:
        title_id = fields.get("title_id")
        if title_id is not None and await session.get(Title, title_id) is None:
            raise BannerError("That title does not exist")
        cleaned["title_id"] = title_id

    for key in ("priority", "is_active", "starts_at", "ends_at"):
        if key in fields:
            cleaned[key] = fields[key]

    starts, ends = cleaned.get("starts_at"), cleaned.get("ends_at")
    if starts and ends and ends <= starts:
        raise BannerError("The end of the window must come after its start")

    return cleaned
