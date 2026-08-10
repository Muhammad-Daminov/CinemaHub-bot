"""
Card shapes, decorations and contrast warnings.

These are the parts of a theme that are *not* colours. All three are
allowlists or pure functions, which is the point: an admin picks from a
fixed set, and nothing they choose can become CSS.

The contrast check is advisory by design — it names the unreadable pair
rather than silently correcting the admin's choice.
"""
import pytest

from app.db.models.theme import ThemeScope
from app.services.themes import (
    CARD_SHAPES,
    CONTRAST_MIN_RATIO,
    DECORATIONS,
    DEFAULT_CARD_SHAPE,
    DEFAULT_DECORATION,
    ThemeError,
    assign_theme,
    contrast_ratio,
    contrast_warnings,
    create_theme,
    resolve_for_user,
    set_default_theme,
    validate_card_shape,
    validate_decoration,
)
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


# ---------- card shapes ----------


@pytest.mark.parametrize("shape", sorted(CARD_SHAPES))
async def test_every_preset_shape_is_accepted(db_session, shape):
    theme = await create_theme(
        db_session, key=f"shape-{shape}", name=shape, tokens={}, card_shape=shape
    )
    assert theme.card_shape == shape


@pytest.mark.parametrize(
    "value",
    ["9999px", "50%", "circle", "12px; background: red", "border-radius: 4px", "../../etc"],
)
async def test_an_arbitrary_radius_is_refused(db_session, value):
    """An admin picks a shape; they never write CSS."""
    with pytest.raises(ThemeError):
        await create_theme(db_session, key="bad-shape", name="Bad", tokens={}, card_shape=value)


async def test_the_shape_defaults_when_unspecified(db_session):
    theme = await create_theme(db_session, key="no-shape", name="X", tokens={})
    assert theme.card_shape == DEFAULT_CARD_SHAPE


async def test_the_resolved_theme_carries_the_shape(db_session):
    theme = await create_theme(
        db_session, key="square-theme", name="Square", tokens={}, card_shape="square"
    )
    await set_default_theme(db_session, theme.id)
    user = await make_user(db_session, 9401)

    assert (await resolve_for_user(db_session, user)).card_shape == "square"


def test_shape_presets_map_to_fixed_radii():
    """The values are ours, not the client's — no radius arrives over the wire."""
    assert set(CARD_SHAPES) == {"square", "soft", "rounded", "extra-rounded"}
    assert all(value.endswith("px") for value in CARD_SHAPES.values())


# ---------- decorations ----------


@pytest.mark.parametrize("decoration", sorted(DECORATIONS))
async def test_every_allowlisted_decoration_is_accepted(db_session, decoration):
    theme = await create_theme(
        db_session, key=f"dec-{decoration}", name=decoration, tokens={}, decoration=decoration
    )
    assert theme.decoration == decoration


@pytest.mark.parametrize(
    "value",
    ["<svg onload=alert(1)>", "https://evil.test/x.svg", "javascript:alert(1)", "custom", "../x"],
)
async def test_an_unknown_decoration_is_refused(db_session, value):
    with pytest.raises(ThemeError):
        await create_theme(db_session, key="bad-dec", name="Bad", tokens={}, decoration=value)


async def test_no_decoration_is_the_default(db_session):
    """A theme without decoration must look completely normal."""
    theme = await create_theme(db_session, key="plain", name="Plain", tokens={})
    assert theme.decoration == DEFAULT_DECORATION == "none"


def test_validators_reject_empty_and_unknown():
    assert validate_card_shape(None) == DEFAULT_CARD_SHAPE
    assert validate_decoration(None) == DEFAULT_DECORATION
    with pytest.raises(ThemeError):
        validate_card_shape("nope")
    with pytest.raises(ThemeError):
        validate_decoration("nope")


# ---------- contrast ----------


def test_contrast_ratio_matches_known_values():
    """Anchored against the WCAG extremes rather than the implementation."""
    assert round(contrast_ratio("#ffffff", "#000000"), 1) == 21.0
    assert round(contrast_ratio("#000000", "#000000"), 1) == 1.0
    # Shorthand hex must behave identically to its expanded form.
    assert round(contrast_ratio("#fff", "#000"), 1) == 21.0


def test_a_readable_palette_produces_no_warnings():
    assert contrast_warnings({"--color-ink": "#ffffff", "--color-bg": "#000000"}) == []


def test_an_unreadable_pair_is_reported_precisely():
    """The warning must name what is unreadable, not just complain."""
    warnings = contrast_warnings(
        {
            "--color-ink": "#777777",
            "--color-bg": "#808080",
            "--color-surface": "#808080",
            "--color-surface-hi": "#808080",
        }
    )
    assert warnings
    first = warnings[0]
    assert first["foreground"].startswith("--color-")
    assert first["background"].startswith("--color-")
    assert first["ratio"] < CONTRAST_MIN_RATIO
    assert first["required"] == CONTRAST_MIN_RATIO
    assert isinstance(first["label"], str) and first["label"]


async def test_a_low_contrast_theme_still_saves(db_session):
    """
    Advisory, not blocking: the admin's colours are never silently
    changed, and a deliberate low-contrast choice is theirs to make.
    """
    theme = await create_theme(
        db_session,
        key="low-contrast",
        name="Low",
        tokens={"--color-ink": "#777777", "--color-bg": "#808080"},
    )
    assert theme.id is not None
    assert contrast_warnings({"--color-ink": "#777777", "--color-bg": "#808080"})


def test_warnings_fill_in_defaults_for_unset_tokens():
    """A partial palette is still checked against what it will actually render with."""
    assert contrast_warnings({}) == []


# ---------- precedence chains the brief calls out ----------


async def test_badge_overrides_interest(db_session):
    from app.db.models.content import ContentType

    from tests.test_themes import _watch  # reuse the 9B-backed helper

    badge = await create_theme(db_session, key="p-badge", name="B", tokens={})
    interest = await create_theme(db_session, key="p-interest", name="I", tokens={})
    await assign_theme(db_session, badge.id, ThemeScope.BADGE, target_value="badge.anime.")
    await assign_theme(
        db_session, interest.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value
    )
    user = await make_user(db_session, 9410)
    await _watch(db_session, user, ContentType.ANIME, 12)

    assert (await resolve_for_user(db_session, user)).key == "p-badge"


async def test_interest_overrides_subscription(db_session):
    from app.db.models.content import ContentType

    from tests.test_themes import _watch

    interest = await create_theme(db_session, key="q-interest", name="I", tokens={})
    subscription = await create_theme(db_session, key="q-sub", name="S", tokens={})
    await assign_theme(
        db_session, interest.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value
    )
    await assign_theme(db_session, subscription.id, ThemeScope.SUBSCRIPTION, target_value="free")
    user = await make_user(db_session, 9411)
    await _watch(db_session, user, ContentType.ANIME, 12)

    assert (await resolve_for_user(db_session, user)).key == "q-interest"


async def test_subscription_overrides_global(db_session):
    subscription = await create_theme(db_session, key="r-sub", name="S", tokens={})
    global_theme = await create_theme(db_session, key="r-global", name="G", tokens={})
    await assign_theme(db_session, subscription.id, ThemeScope.SUBSCRIPTION, target_value="free")
    await assign_theme(db_session, global_theme.id, ThemeScope.GLOBAL)
    user = await make_user(db_session, 9412)

    assert (await resolve_for_user(db_session, user)).key == "r-sub"


async def test_b_to_c_to_b_stays_stable(db_session):
    """The B → C → B ordering the brief asks for, on top of A → B → A."""
    from app.db.models.content import ContentType

    from tests.test_themes import _watch

    drama = await create_theme(db_session, key="s-drama", name="D", tokens={})
    base = await create_theme(db_session, key="s-base", name="Base", tokens={})
    await assign_theme(
        db_session, drama.id, ThemeScope.INTEREST, target_value=ContentType.DRAMA.value
    )
    await assign_theme(db_session, base.id, ThemeScope.GLOBAL)

    b = await make_user(db_session, 9413)
    c = await make_user(db_session, 9414)
    await _watch(db_session, b, ContentType.DRAMA, 12)

    assert (await resolve_for_user(db_session, b)).key == "s-drama"
    assert (await resolve_for_user(db_session, c)).key == "s-base"
    assert (await resolve_for_user(db_session, b)).key == "s-drama"


# ---------- editing shape and decoration ----------
#
# Until now `set_tokens` saved colours only, so the editor's shape
# dropdown changed local state and silently discarded it, and a
# decoration could be set at creation and never afterwards. These pin the
# write path the admin picker depends on.


async def test_saving_a_theme_persists_its_decoration_and_shape(db_session):
    from app.services.themes import set_tokens

    theme = await create_theme(
        db_session, key="deco", name="Deco", tokens={"--color-bg": "#101010"}
    )
    assert theme.decoration == DEFAULT_DECORATION

    saved = await set_tokens(
        db_session, theme.id, {"--color-bg": "#202020"}, card_shape="square", decoration="cinema"
    )

    assert saved.decoration == "cinema"
    assert saved.card_shape == "square"
    assert {token.token: token.value for token in saved.tokens}["--color-bg"] == "#202020"


async def test_saving_only_colours_leaves_shape_and_decoration_alone(db_session):
    """
    Omitted means "leave as it is". Defaulting to the fallback instead
    would let an admin editing one colour silently wipe a decoration they
    set earlier.
    """
    from app.services.themes import set_tokens

    theme = await create_theme(
        db_session,
        key="keepdeco",
        name="Keep",
        tokens={"--color-bg": "#101010"},
        card_shape="soft",
        decoration="stars",
    )

    saved = await set_tokens(db_session, theme.id, {"--color-bg": "#303030"})

    assert saved.decoration == "stars"
    assert saved.card_shape == "soft"


@pytest.mark.parametrize(
    "value",
    [
        "<svg onload=alert(1)>",
        "https://evil.example.com/x.svg",
        "url(evil.svg)",
        "stars; drop table chp_themes",
        "STARS",
        "../../etc/passwd",
    ],
)
async def test_an_unsafe_decoration_cannot_be_saved(db_session, value):
    """
    The stored value is a key naming a compiled component. Markup, a URL
    or anything outside the allowlist is refused at the write, so nothing
    a renderer would have to interpret can ever reach the database.
    """
    from app.services.themes import set_tokens

    theme = await create_theme(
        db_session, key=f"unsafe{abs(hash(value)) % 10000}", name="Unsafe", tokens={}
    )
    with pytest.raises(ThemeError):
        await set_tokens(db_session, theme.id, {}, decoration=value)


# ---------- the frontend contract ----------


def _frontend_source(name: str) -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    return (root / "webapp" / "src" / name).read_text(encoding="utf-8")


def test_the_frontend_compiles_exactly_the_decorations_the_server_allows():
    """
    Two halves of one allowlist. A key the server accepts but the frontend
    cannot draw renders nothing; a key the frontend offers but the server
    rejects fails on save. Either way the admin picker would be lying, so
    the two lists are pinned together here.
    """
    import re

    source = _frontend_source("components/DecorationLayer.tsx")
    block = source.split("const DECORATIONS: Record<string, () => JSX.Element> = {", 1)[1]
    block = block.split("};", 1)[0]
    compiled = set(re.findall(r"^\s*(\w+):", block, re.M))

    # "none" is real but draws nothing, so it has no entry in the map.
    assert compiled | {"none"} == set(DECORATIONS)


def test_every_decoration_has_a_name_in_every_language():
    """The picker labels itself from the catalog, so a missing name would
    render a raw key like `theme.decoration.cinema` as a button label."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for language in ("uz", "ru", "en"):
        catalog = json.loads((root / "app" / "locales" / f"{language}.json").read_text("utf-8"))
        for name in DECORATIONS:
            key = f"theme.decoration.{name}"
            assert key in catalog, f"{key} missing from {language}"
            assert catalog[key].strip(), f"{key} is blank in {language}"


async def test_an_empty_decoration_means_none(db_session):
    """Blank normalises to the default rather than erroring — clearing the
    field is a legitimate way to say "no decoration"."""
    from app.services.themes import set_tokens

    theme = await create_theme(
        db_session, key="blankdeco", name="Blank", tokens={}, decoration="stars"
    )
    saved = await set_tokens(db_session, theme.id, {}, decoration="")
    assert saved.decoration == DEFAULT_DECORATION


def test_the_decoration_picker_cannot_touch_the_document_root():
    """
    Preview isolation, asserted against the source. The picker and the
    preview render the real component inside their own containers; if
    either ever reaches for the document root, an admin trying decorations
    would restyle the panel they are working in.

    Comment lines are stripped first: this file *documents* that it never
    uses these APIs, and a naive substring search would match the promise
    instead of a violation.
    """
    panel = _frontend_source("admin/AppearancePanel.tsx")
    code = "\n".join(
        line
        for line in panel.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )

    for forbidden in (
        "document.documentElement",
        "setProperty",
        "dangerouslySetInnerHTML",
        "innerHTML",
        "new Function",
    ):
        assert forbidden not in code, f"AppearancePanel must not use {forbidden}"


def test_the_live_decoration_layer_is_inert_and_behind_the_app():
    """A decoration must never intercept a tap or cover content."""
    layer = _frontend_source("components/DecorationLayer.tsx")

    assert "pointer-events-none" in layer
    assert "-z-10" in layer
    # An unknown key draws nothing rather than throwing.
    assert "if (!DECORATIONS[name]) return null;" in layer
