"""
The release version contract.

One number, read from the root VERSION file, reported in two places: the
Mini App's Settings screen (baked into the bundle by Vite) and `/health`'s
`app_version`. The frontend half cannot be asserted from here — there is
no JS test runner — so what these tests protect is the source of truth and
the backend half of the contract.

The point of the last two tests is narrow and deliberate: `/health` is
read by Render's health check and an uptime monitor, and `version` there
has always meant the short commit. Adding `app_version` beside it must not
change what any existing field means.
"""
import re

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.core.version import APP_VERSION, _VERSION_FILE
from app.main import app

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


@pytest.fixture
def stub_db(monkeypatch):
    """
    `/health` probes the database, and `settings.DATABASE_URL` is
    production. Stubbed for the same reason test_rate_limit.py stubs it:
    the version contract is under test, not the health probe.
    """

    async def fake_check():
        return True

    monkeypatch.setattr(main_module, "check_db_connection", fake_check)


def test_the_version_file_holds_a_semver():
    """
    Releases are tagged vMAJOR.MINOR.PATCH, so anything else in this file
    would produce a tag that does not describe a release.
    """
    assert SEMVER.match(_VERSION_FILE.read_text(encoding="utf-8").strip())


def test_app_version_is_read_from_the_file():
    """No second source: the constant is the file, not a copy of it."""
    assert APP_VERSION == _VERSION_FILE.read_text(encoding="utf-8").strip()


async def test_health_reports_the_release_version(stub_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get("/health")).json()

    assert body["app_version"] == APP_VERSION


async def test_health_keeps_its_existing_fields_unchanged(stub_db):
    """
    `status` is the contract Render and the uptime monitor read, and
    `version` is build identity — the short commit — which something
    outside this repository may already consume. Adding a field must not
    redefine either.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get("/health")).json()

    assert body["status"] == "ok"
    assert "commit" in body
    # Still the commit, never the release version — the two are different
    # questions and conflating them would make a deploy unverifiable.
    assert body["version"] != APP_VERSION or APP_VERSION == "unknown"
    assert body["version"] == (
        body["commit"][:7] if body["commit"] != "unknown" else "development"
    )
