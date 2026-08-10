"""
Hero carousel autoplay: the invariants that keep it rotating.

**These are source assertions, not behavioural tests.** There is no
frontend test runner in this repository, so nothing here proves the
carousel advances in a browser — only that the specific structures which
broke it cannot quietly come back. That distinction is the point: the
original bug shipped while every backend test was green.

What broke: `HeroBanner` held a `paused` boolean set to `true` by
`onPointerDown` on the whole banner and never set back to `false`
anywhere. `pointerdown` fires when a *scroll* begins, and the banner is
the first element in the feed, so a user's first swipe ended rotation for
the session. Every assertion below guards one link in that chain.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANNER = ROOT / "webapp" / "src" / "components" / "HeroBanner.tsx"
APP = ROOT / "webapp" / "src" / "App.tsx"


def _code(path: Path) -> str:
    """Source with comment lines stripped — these files *describe* the bug."""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )


# ---------- the latch cannot come back ----------


def test_the_carousel_holds_no_pause_flag_at_all():
    """
    Structural, not a corrected boolean. A flag that stops autoplay needs
    a release path on every route out, and the bug was one missing route.
    Having no flag is what makes the class of bug unreachable.
    """
    code = _code(BANNER)
    assert "setPaused" not in code
    assert not re.search(r"\bpaused\b", code), "a pause flag reappeared in HeroBanner"


def test_a_pointer_press_is_not_treated_as_engagement():
    """
    `pointerdown` fires at the start of a scroll as readily as a tap. This
    is the exact line that killed autoplay on the first swipe.
    """
    code = _code(BANNER)
    for handler in ("onPointerDown", "onTouchStart", "onPointerCancel", "onTouchCancel"):
        assert handler not in code, f"{handler} must not gate autoplay"


def test_autoplay_is_gated_only_on_self_releasing_conditions():
    """
    Every condition that can stop rotation must be able to become false
    again without a user gesture: an OS setting, a browser event, or the
    number of slides. Nothing a finger sets.
    """
    code = _code(BANNER)
    guard = re.search(r"if \((.*?)\) return;\s*\n\s*const timer = window\.setInterval", code, re.S)
    assert guard, "the rotation guard is no longer recognisable"

    conditions = {part.strip() for part in guard.group(1).split("||")}
    assert conditions == {"reducedMotion", "hidden", "movies.length < 2"}, conditions


# ---------- timers ----------


def test_exactly_one_interval_is_created_and_always_cleared():
    """A timer created without cleanup accumulates one per re-render."""
    code = _code(BANNER)
    assert code.count("setInterval") == 1
    assert code.count("clearInterval") == 1


def test_the_tick_uses_the_updater_form():
    """
    `setIndex(current => ...)` — without it the callback closes over a
    stale index and the timer would have to be rebuilt every tick.
    """
    code = _code(BANNER)
    assert "setIndex((current) => (current + 1) % movies.length)" in code


def test_the_rotation_effect_depends_on_everything_it_reads():
    """A missing dependency is how a timer keeps running on stale state."""
    code = _code(BANNER)
    deps = re.search(
        r"window\.clearInterval\(timer\);\s*\n\s*\}, \[(.*?)\]\);", code, re.S
    )
    assert deps, "the rotation effect's dependency list is no longer recognisable"

    listed = {part.strip() for part in deps.group(1).split(",") if part.strip()}
    assert listed == {"reducedMotion", "hidden", "movies.length", "restartToken"}, listed


def test_the_interval_is_six_seconds():
    assert "const ROTATE_MS = 6000;" in _code(BANNER)


def test_every_listener_added_is_removed():
    """Reduced-motion and visibility both subscribe; both must unsubscribe."""
    code = _code(BANNER)
    assert code.count("addEventListener") == code.count("removeEventListener") == 2


# ---------- deliberate interaction ----------


def test_a_dot_tap_restarts_the_countdown_rather_than_stopping_it():
    code = _code(BANNER)
    assert "setRestartToken((token) => token + 1)" in code
    # The token only ever grows, so it can only ever rebuild the timer.
    assert "setRestartToken(0)" not in code


def test_backgrounding_is_released_by_a_browser_event():
    """
    The one condition that pauses on its own must be un-paused by
    something that is not a gesture, or a backgrounded app that never
    returns focus would strand the carousel.
    """
    code = _code(BANNER)
    assert 'document.addEventListener("visibilitychange", sync)' in code
    assert 'document.removeEventListener("visibilitychange", sync)' in code


# ---------- slide data ----------


def test_the_fallback_prefers_posters_but_never_drops_below_two():
    """
    Posterless slides render as a gradient, so rotation through them looks
    broken. Filtering them out entirely would be worse: below two slides
    the timer does not start at all, turning a cosmetic problem into a
    functional one.
    """
    code = _code(APP)
    assert "const withPosters = pool.filter((movie) => movie.poster_url);" in code
    assert "withPosters.length >= 2 ? withPosters : pool" in code


def test_campaign_slides_still_take_precedence_over_the_fallback():
    """The poster preference must not have disturbed configured campaigns."""
    code = _code(APP)
    assert "const bannerMovies: Movie[] = campaignSlides.length" in code
    assert "campaignSlides.map(" in code


def test_a_campaign_without_a_movie_or_a_poster_is_dropped_before_rendering():
    """Malformed campaign data must not reach the carousel as a blank slide."""
    code = _code(APP)
    assert (
        "const campaignSlides = slides.filter((slide) => slide.movie || slide.poster_url);"
        in code
    )


def test_the_carousel_renders_nothing_rather_than_crashing_with_no_slides():
    code = _code(BANNER)
    assert "if (!active) return null;" in code


def test_the_index_is_clamped_when_the_slide_list_shrinks():
    """Rows arrive progressively; an index past the end would render blank."""
    code = _code(BANNER)
    assert "setIndex((current) => (current < movies.length ? current : 0));" in code


def test_the_dots_are_only_shown_when_there_is_something_to_navigate():
    code = _code(BANNER)
    assert "{movies.length > 1 && (" in code


def test_the_dots_stay_accessible():
    """Keyboard and screen-reader affordances must survive the rework."""
    code = _code(BANNER)
    assert "aria-label={`${slide + 1}-banner`}" in code
    assert "aria-current={slide === index}" in code
