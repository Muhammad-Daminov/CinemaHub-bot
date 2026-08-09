"""
/health reports what is actually running.

A failed Render deploy leaves the *previous* build serving traffic, and
that is exactly what happened: production ran a two-phase-old release for
over a day while `/health` cheerfully returned `{"status": "ok"}`. The
only way to tell was to fetch `/openapi.json` and count routes.

`status` keeps its exact previous shape and value — the Render health
check and an uptime monitor both read it, and this is not the place to
change a contract two external systems depend on.
"""
import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.core.config import settings
from app.main import app


@pytest.fixture
async def client(monkeypatch):
    """
    The database probe is stubbed: `settings.DATABASE_URL` points at
    production and a test must never open a connection to it.
    """

    async def fake_check():
        return True

    monkeypatch.setattr(main_module, "check_db_connection", fake_check)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_status_is_unchanged(client):
    """Render and UptimeRobot read this field. It must keep its old shape."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_the_running_commit_is_reported(client, monkeypatch):
    monkeypatch.setattr(settings, "RENDER_GIT_COMMIT", "3c1133b3ae2e8022478b640be16f2250b4adc26d")

    payload = (await client.get("/health")).json()
    assert payload["commit"] == "3c1133b3ae2e8022478b640be16f2250b4adc26d"
    assert payload["version"] == "3c1133b", "the short hash is what a human compares against git log"


async def test_a_local_run_says_so_rather_than_guessing(client, monkeypatch):
    """
    RENDER_GIT_COMMIT is injected by Render and absent locally. Deriving a
    commit from the working copy would report the checkout rather than
    what is deployed, which is worse than admitting it is unknown.
    """
    monkeypatch.setattr(settings, "RENDER_GIT_COMMIT", None)

    payload = (await client.get("/health")).json()
    assert payload["commit"] == "unknown"
    assert payload["version"] == "development"


async def test_head_requests_still_work(client):
    """UptimeRobot probes with HEAD."""
    assert (await client.head("/health")).status_code == 200


async def test_the_deployed_version_is_comparable_to_a_git_hash(client, monkeypatch):
    """
    The whole point: `curl /health` next to `git rev-parse --short HEAD`
    answers "is my commit live?" without fingerprinting the OpenAPI schema.
    """
    monkeypatch.setattr(settings, "RENDER_GIT_COMMIT", "abcdef1234567890")
    payload = (await client.get("/health")).json()

    assert payload["commit"].startswith(payload["version"])
    assert len(payload["version"]) == 7
