"""
Translation lookup and the Uzbek fallback.

The fallback is the load-bearing part: the backend merges the requested
language over uz before sending, so a key present only in uz must still
resolve for a Russian user rather than rendering as raw key text.
"""
import pytest

from app.core.i18n import CATALOGS, FALLBACK_LANGUAGE, t
from app.db.models.user import UILanguage


def test_all_languages_have_catalogs():
    assert set(CATALOGS) == set(UILanguage)
    assert all(catalog for catalog in CATALOGS.values())


def test_fallback_is_uzbek():
    assert FALLBACK_LANGUAGE == UILanguage.UZ


def test_catalogs_agree_on_keys():
    """The parity guarantee scripts/check_locales.py enforces, asserted here too."""
    key_sets = {lang: set(catalog) for lang, catalog in CATALOGS.items()}
    reference = key_sets[UILanguage.UZ]
    for lang, keys in key_sets.items():
        assert keys == reference, f"{lang.value} differs from uz by {keys ^ reference}"


def test_translates_into_each_language():
    rendered = {lang: t("common.lang_uz", lang) for lang in UILanguage}
    assert all(isinstance(value, str) and value for value in rendered.values())


def test_unknown_key_returns_the_key_itself():
    """A visible 'app.nope' is a bug report; a blank string or crash is a mystery."""
    assert t("app.definitely_not_a_real_key") == "app.definitely_not_a_real_key"


def test_placeholders_are_substituted():
    assert "Tester" in t("start.welcome", UILanguage.UZ, name="Tester")


def test_missing_placeholder_does_not_raise():
    """A template arg the caller forgot must degrade, not take down a handler."""
    assert t("start.welcome", UILanguage.UZ)


@pytest.mark.parametrize("lang", list(UILanguage))
def test_every_key_renders_in_every_language(lang):
    """Guards against a malformed template — an unbalanced brace, say."""
    for key in CATALOGS[FALLBACK_LANGUAGE]:
        assert isinstance(t(key, lang), str)
