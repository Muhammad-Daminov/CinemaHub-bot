"""
Theme resolution: which palette one viewer gets, and why.

**Precedence is written down once, here, and tested.** When several rules
match a user, exactly one theme wins:

    USER  >  BADGE  >  INTEREST  >  SUBSCRIPTION  >  GLOBAL

An explicit assignment to a person beats any group they belong to; a
badge they earned beats a segment they merely fall into; anything beats
the default. Within one scope, higher `priority` wins, then lower id — so
the answer is total, never "whichever row came back first".

**Resolution is per user and never cached across users.** There is no
module-level state here at all. The inputs are the caller's own interest
profile (Phase 9B) and their own subscription; the output is built fresh.
A cache keyed on anything less than the user is precisely how one
person's UI would appear for another.

**A broken theme cannot break the app.** Every layer falls back: an
assignment pointing at a disabled theme is ignored, a theme missing
tokens inherits the defaults below, and if the database has no themes at
all the built-in palette is returned. The frontend applies whatever it
receives on top of its own compiled-in stylesheet, so even an empty
response renders the app exactly as it looks today.

**Nothing here can inject CSS.** Token names come from a fixed allowlist
and values must match a colour pattern; anything else is refused at write
time. The frontend writes them with `style.setProperty`, never into
markup.
"""
import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.theme import Theme, ThemeAssignment, ThemeScope, ThemeToken
from app.db.models.user import User
from app.services.personalization import get_profile
from app.services.subscriptions import is_user_premium

logger = logging.getLogger(__name__)

# The vocabulary. These are the CSS custom properties the frontend already
# uses — 32 components consume them through Tailwind — plus the tokens
# this phase adds. Extending the app's palette means adding a name here,
# never accepting an arbitrary one from an admin form.
#
# Mapping to the semantic names in the brief:
#   background -> bg, surface/surface-secondary -> surface/surface-hi,
#   text/text-secondary -> ink/ink-dim, primary/accent -> marquee,
#   danger -> premiere.
DEFAULT_TOKENS: dict[str, str] = {
    "--color-bg": "#0a0a0d",
    "--color-surface": "#17171c",
    "--color-surface-hi": "#1f1f26",
    "--color-ink": "#f5f5f7",
    "--color-ink-dim": "#9a9aa5",
    "--color-marquee": "#e8b84b",
    "--color-marquee-dim": "#b88f35",
    "--color-premiere": "#d64550",
    # Added by this phase. Episode state is configurable because the brief
    # asks for it specifically; success/warning/danger round out the set
    # so status UI stops hardcoding Tailwind palette colours.
    "--color-episode-watched": "#3fb950",
    "--color-episode-unwatched": "#1f1f26",
    "--color-episode-check": "#3fb950",
    "--color-success": "#3fb950",
    "--color-warning": "#d29922",
    "--color-danger": "#d64550",
}

ALLOWED_TOKENS = frozenset(DEFAULT_TOKENS)

# #rgb, #rrggbb or #rrggbbaa. Deliberately not a general CSS colour: no
# `url()`, no `var()`, no functional notation — the narrower the grammar,
# the smaller the surface for anything to hide in.
_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# Highest first. The one place precedence is defined.
SCOPE_PRECEDENCE: tuple[ThemeScope, ...] = (
    ThemeScope.USER,
    ThemeScope.BADGE,
    ThemeScope.INTEREST,
    ThemeScope.SUBSCRIPTION,
    ThemeScope.GLOBAL,
)

DEFAULT_THEME_KEY = "default"

# Card shapes, as fixed presets mapped to a radius the frontend applies
# through a token. An allowlist rather than a free border-radius: an admin
# picks a shape, they do not write CSS. "pill" is deliberately absent from
# posters — a pill-shaped 2:3 poster is unreadable — and is offered only
# where it makes sense.
CARD_SHAPES: dict[str, str] = {
    "square": "0px",
    "soft": "4px",
    "rounded": "12px",
    "extra-rounded": "20px",
}
DEFAULT_CARD_SHAPE = "rounded"

# Decorative overlays. Each name maps to an asset the frontend already
# ships; nothing here is a URL or markup, so a decoration cannot carry
# anything executable. "none" is the default and must stay first-class —
# a theme without decoration has to look completely normal.
DECORATIONS: frozenset[str] = frozenset(
    {"none", "stars", "cinema", "anime", "horror", "abstract", "seasonal"}
)
DEFAULT_DECORATION = "none"

# Relative luminance below which a colour counts as "dark". Used by the
# contrast check, which is deliberately a plain WCAG ratio rather than a
# dependency.
CONTRAST_MIN_RATIO = 4.5


class ThemeError(Exception):
    """Raised when a theme or assignment is not acceptable."""


@dataclass(frozen=True)
class ResolvedTheme:
    """The palette one viewer should render with, and where it came from."""

    key: str
    name: str
    tokens: dict[str, str]
    card_shape: str
    decoration: str
    # Which rule won, for explainability in the admin panel and in tests.
    scope: ThemeScope | None


def validate_token(token: str) -> str:
    if token not in ALLOWED_TOKENS:
        raise ThemeError(f"Unknown design token: {token}")
    return token


def validate_color(value: str) -> str:
    """
    Colours only, in hex.

    A value here becomes a CSS custom property. Accepting general CSS
    would let an admin write `red; background: url(javascript:...)` and
    turn a theme form into an injection point, so the grammar is kept as
    narrow as the feature allows.
    """
    cleaned = (value or "").strip()
    if not _COLOR.match(cleaned):
        raise ThemeError(f"Invalid colour: {value!r} — use #rgb, #rrggbb or #rrggbbaa")
    return cleaned.lower()


def validate_card_shape(value: str | None) -> str:
    """One of the presets. Anything else — including a raw radius — is refused."""
    cleaned = (value or DEFAULT_CARD_SHAPE).strip()
    if cleaned not in CARD_SHAPES:
        raise ThemeError(f"Unknown card shape: {value!r}")
    return cleaned


def validate_decoration(value: str | None) -> str:
    cleaned = (value or DEFAULT_DECORATION).strip()
    if cleaned not in DECORATIONS:
        raise ThemeError(f"Unknown decoration: {value!r}")
    return cleaned


def _relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance. Plain arithmetic — no dependency needed."""
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    adjusted = [
        channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two hex colours, 1.0–21.0."""
    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# Foreground/background pairs that must stay readable. Text on its own
# surface, and the label on the accent button.
CONTRAST_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("--color-ink", "--color-bg", "Body text on the background"),
    ("--color-ink", "--color-surface", "Text on cards"),
    ("--color-ink-dim", "--color-surface", "Secondary text on cards"),
    ("--color-ink", "--color-surface-hi", "Text on raised surfaces"),
)


def contrast_warnings(tokens: dict[str, str]) -> list[dict[str, object]]:
    """
    Readability problems in a palette, as warnings rather than errors.

    Deliberately advisory: the admin's colours are never silently changed,
    and a deliberate low-contrast choice is theirs to make. Each warning
    names the exact pair so the message can say *what* is unreadable
    instead of just "bad colours".
    """
    merged = _merge(tokens)
    warnings: list[dict[str, object]] = []
    for foreground, background, label in CONTRAST_PAIRS:
        ratio = contrast_ratio(merged[foreground], merged[background])
        if ratio < CONTRAST_MIN_RATIO:
            warnings.append(
                {
                    "foreground": foreground,
                    "background": background,
                    "label": label,
                    "ratio": round(ratio, 2),
                    "required": CONTRAST_MIN_RATIO,
                }
            )
    return warnings


def validate_tokens(tokens: dict[str, str]) -> dict[str, str]:
    """Validates a whole palette, rejecting the batch if any member is bad."""
    return {validate_token(name): validate_color(value) for name, value in tokens.items()}


def _merge(tokens: dict[str, str]) -> dict[str, str]:
    """
    A theme's tokens over the defaults.

    Partial themes are legitimate — "the anime theme, but only the accent
    differs" should not require restating twelve colours — and a missing
    token must never leave a component with no value.
    """
    return {**DEFAULT_TOKENS, **tokens}


def builtin_theme() -> ResolvedTheme:
    """The palette compiled into the frontend. The floor under every fallback."""
    return ResolvedTheme(
        key=DEFAULT_THEME_KEY,
        name="Default",
        tokens=dict(DEFAULT_TOKENS),
        card_shape=DEFAULT_CARD_SHAPE,
        decoration=DEFAULT_DECORATION,
        scope=None,
    )


def _to_resolved(theme: Theme, scope: ThemeScope | None) -> ResolvedTheme:
    return ResolvedTheme(
        key=theme.key,
        name=theme.name,
        tokens=_merge({token.token: token.value for token in theme.tokens}),
        card_shape=theme.card_shape or DEFAULT_CARD_SHAPE,
        decoration=theme.decoration or DEFAULT_DECORATION,
        scope=scope,
    )


async def default_theme(session: AsyncSession) -> ResolvedTheme:
    """The configured default, or the built-in palette when none exists."""
    result = await session.execute(
        select(Theme).where(Theme.is_default.is_(True), Theme.is_active.is_(True)).limit(1)
    )
    theme = result.scalar_one_or_none()
    return _to_resolved(theme, ThemeScope.GLOBAL) if theme else builtin_theme()


def _matches(
    assignment: ThemeAssignment,
    user: User,
    badge_key: str | None,
    dominant_type: str | None,
    premium: bool,
) -> bool:
    """Whether one rule applies to this viewer."""
    if assignment.scope == ThemeScope.GLOBAL:
        return True
    if assignment.scope == ThemeScope.USER:
        return assignment.user_id == user.id
    if assignment.scope == ThemeScope.BADGE:
        # Prefix match, so "badge.anime." covers every anime tier with one
        # rule instead of one per tier.
        return (
            badge_key is not None
            and assignment.target_value is not None
            and badge_key.startswith(assignment.target_value)
        )
    if assignment.scope == ThemeScope.INTEREST:
        return dominant_type is not None and assignment.target_value == dominant_type
    if assignment.scope == ThemeScope.SUBSCRIPTION:
        return assignment.target_value == ("premium" if premium else "free")
    return False


async def resolve_for_user(session: AsyncSession, user: User) -> ResolvedTheme:
    """
    The theme this viewer gets.

    Reads their own interest profile and subscription — no id is taken
    from the request anywhere in this path, so there is no way to ask for
    somebody else's theme.
    """
    assignments = (
        await session.execute(
            select(ThemeAssignment)
            .join(Theme, Theme.id == ThemeAssignment.theme_id)
            .where(
                ThemeAssignment.is_active.is_(True),
                # An assignment pointing at a disabled theme is ignored
                # rather than applied: switching a theme off must actually
                # switch it off, wherever it was referenced.
                Theme.is_active.is_(True),
            )
            .order_by(ThemeAssignment.priority.desc(), ThemeAssignment.id)
        )
    ).scalars().all()

    if not assignments:
        return await default_theme(session)

    # Only computed when a rule could need them, so a platform using a
    # single global theme pays for nothing.
    needs_profile = any(
        a.scope in (ThemeScope.BADGE, ThemeScope.INTEREST) for a in assignments
    )
    needs_subscription = any(a.scope == ThemeScope.SUBSCRIPTION for a in assignments)

    badge_key = dominant_type = None
    if needs_profile:
        profile = await get_profile(session, user.id)
        badge_key, dominant_type = profile.badge_key, profile.dominant_type
    premium = await is_user_premium(session, user.id) if needs_subscription else False

    for scope in SCOPE_PRECEDENCE:
        for assignment in assignments:
            if assignment.scope != scope:
                continue
            if not _matches(assignment, user, badge_key, dominant_type, premium):
                continue
            theme = await session.get(Theme, assignment.theme_id)
            if theme is None or not theme.is_active:
                continue
            return _to_resolved(theme, scope)

    return await default_theme(session)


# ---------- administration ----------


async def list_themes(session: AsyncSession) -> list[Theme]:
    result = await session.execute(select(Theme).order_by(Theme.is_default.desc(), Theme.id))
    return list(result.scalars())


async def create_theme(
    session: AsyncSession,
    key: str,
    name: str,
    tokens: dict[str, str],
    description: str | None = None,
    card_shape: str | None = None,
    decoration: str | None = None,
) -> Theme:
    cleaned_key = (key or "").strip().lower()
    if not re.match(r"^[a-z0-9_-]{2,64}$", cleaned_key):
        raise ThemeError("Key must be 2–64 characters of a–z, 0–9, _ or -")
    if not (name or "").strip():
        raise ThemeError("A theme needs a name")

    existing = await session.execute(select(Theme).where(Theme.key == cleaned_key))
    if existing.scalar_one_or_none() is not None:
        raise ThemeError("A theme with that key already exists")

    theme = Theme(
        key=cleaned_key,
        name=name.strip(),
        description=(description or "").strip() or None,
        card_shape=validate_card_shape(card_shape),
        decoration=validate_decoration(decoration),
    )
    session.add(theme)
    await session.flush()

    for token, value in validate_tokens(tokens).items():
        session.add(ThemeToken(theme_id=theme.id, token=token, value=value))
    await session.flush()
    await session.refresh(theme)
    return theme


async def set_tokens(session: AsyncSession, theme_id: int, tokens: dict[str, str]) -> Theme | None:
    """Replaces the named tokens. Tokens not mentioned keep their value."""
    theme = await session.get(Theme, theme_id)
    if theme is None:
        return None

    cleaned = validate_tokens(tokens)
    current = {token.token: token for token in theme.tokens}
    for name, value in cleaned.items():
        if name in current:
            current[name].value = value
        else:
            session.add(ThemeToken(theme_id=theme.id, token=name, value=value))
    await session.flush()
    await session.refresh(theme)
    return theme


async def set_default_theme(session: AsyncSession, theme_id: int) -> Theme | None:
    """
    Moves the default flag. Exactly one theme holds it at a time.

    The old default is cleared in the same transaction, so there is never
    a moment with two defaults or none.
    """
    theme = await session.get(Theme, theme_id)
    if theme is None:
        return None
    if not theme.is_active:
        raise ThemeError("A disabled theme cannot be the default")

    for other in await list_themes(session):
        other.is_default = other.id == theme.id
    await session.flush()
    return theme


async def set_theme_active(session: AsyncSession, theme_id: int, is_active: bool) -> Theme | None:
    theme = await session.get(Theme, theme_id)
    if theme is None:
        return None
    if theme.is_default and not is_active:
        raise ThemeError("The default theme cannot be disabled — set another default first")
    theme.is_active = is_active
    await session.flush()
    return theme


async def delete_theme(session: AsyncSession, theme_id: int) -> bool:
    """
    Removes a theme and its assignments.

    The default is protected: deleting the fallback would leave users
    resolving to nothing, and that is a state an admin form must not be
    able to create.
    """
    theme = await session.get(Theme, theme_id)
    if theme is None:
        return False
    if theme.is_default:
        raise ThemeError("The default theme cannot be deleted — set another default first")
    await session.delete(theme)
    await session.flush()
    return True


async def duplicate_theme(session: AsyncSession, theme_id: int, key: str, name: str) -> Theme | None:
    """Copies a palette — the usual way a new theme starts."""
    source = await session.get(Theme, theme_id)
    if source is None:
        return None
    return await create_theme(
        session,
        key=key,
        name=name,
        tokens={token.token: token.value for token in source.tokens},
        description=source.description,
    )


async def assign_theme(
    session: AsyncSession,
    theme_id: int,
    scope: ThemeScope,
    *,
    user_id: int | None = None,
    target_value: str | None = None,
    priority: int = 0,
) -> ThemeAssignment:
    """Creates a rule, validating that the scope has what it needs."""
    theme = await session.get(Theme, theme_id)
    if theme is None:
        raise ThemeError("That theme does not exist")

    cleaned = (target_value or "").strip() or None
    if scope == ThemeScope.USER:
        if user_id is None:
            raise ThemeError("A user assignment needs a user")
        if await session.get(User, user_id) is None:
            raise ThemeError("That user does not exist")
        cleaned = None
    elif scope == ThemeScope.GLOBAL:
        user_id, cleaned = None, None
    else:
        user_id = None
        if not cleaned:
            raise ThemeError(f"{scope.value} assignment needs a target value")
        if len(cleaned) > 64:
            raise ThemeError("Target value is too long")
        if scope == ThemeScope.SUBSCRIPTION and cleaned not in ("premium", "free"):
            raise ThemeError("Subscription target must be 'premium' or 'free'")

    assignment = ThemeAssignment(
        theme_id=theme_id, scope=scope, user_id=user_id, target_value=cleaned, priority=priority
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def list_assignments(session: AsyncSession) -> list[ThemeAssignment]:
    result = await session.execute(
        select(ThemeAssignment).order_by(ThemeAssignment.scope, ThemeAssignment.priority.desc())
    )
    return list(result.scalars())


async def delete_assignment(session: AsyncSession, assignment_id: int) -> bool:
    assignment = await session.get(ThemeAssignment, assignment_id)
    if assignment is None:
        return False
    await session.delete(assignment)
    await session.flush()
    return True
