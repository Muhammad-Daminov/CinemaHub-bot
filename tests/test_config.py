"""
Settings parsing.

`admin_ids_list` is an authorization input — it decides who is an
administrator — so its parsing is worth pinning down rather than
assuming.
"""
from app.core.config import Settings


def _settings(**overrides) -> Settings:
    """Builds Settings without reading the real .env for the overridden fields."""
    base = {
        "BOT_TOKEN": "test-token",
        "TMDB_API_KEY": "test-key",
        "GEMINI_API_KEY": "test-key",
    }
    return Settings(**{**base, **overrides})


def test_gemini_model_default_is_the_one_that_was_in_force():
    """
    There were two GEMINI_MODEL declarations and the second silently won,
    so editing the first had no effect. Asserts the *declared default*
    rather than settings.GEMINI_MODEL, because the deployed .env sets
    this variable and would mask a regression in the declaration.
    """
    assert Settings.model_fields["GEMINI_MODEL"].default == "gemini-2.5-flash"


def test_gemini_model_is_still_overridable_by_environment():
    assert _settings(GEMINI_MODEL="gemini-flash-latest").GEMINI_MODEL == "gemini-flash-latest"


def test_admin_ids_parse_from_csv():
    assert _settings(ADMIN_IDS="111,222,333").admin_ids_list == [111, 222, 333]


def test_admin_ids_tolerate_whitespace_and_trailing_comma():
    assert _settings(ADMIN_IDS=" 111 , 222 ,").admin_ids_list == [111, 222]


def test_empty_admin_ids_yields_no_admins():
    """An unset ADMIN_IDS must mean nobody, never everybody."""
    assert _settings(ADMIN_IDS="").admin_ids_list == []


def test_topup_presets_parse_from_csv():
    assert _settings(TOPUP_PRESET_AMOUNTS="1000,5000").topup_preset_amounts_list == [1000, 5000]
