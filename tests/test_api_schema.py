"""
OpenAPI schema generation.

Exists because of a real regression: removing an import that a linter
called unused broke schema generation while leaving `import app.main`
perfectly happy. Pydantic resolves response-model annotations lazily, so
a broken one surfaces only when the schema is built — which in practice
meant when someone opened /docs.

`import app.main` cannot catch that class of fault, and neither can a
service-level test. Building the schema does — and since production no
longer serves /docs, this test is now the *only* thing that would catch
it before a user did.
"""
import pytest

from app.main import app, docs_urls


@pytest.fixture(scope="module")
def schema() -> dict:
    return app.openapi()


def test_openapi_schema_builds(schema):
    """Forces every response model to resolve its annotations."""
    assert schema["info"]["title"]
    assert schema["paths"]


def test_every_router_is_represented(schema):
    prefixes = ("/api/auth", "/api/i18n", "/api/movies", "/api/admin")
    for prefix in prefixes:
        assert any(path.startswith(prefix) for path in schema["paths"]), f"no routes under {prefix}"


def test_activity_point_resolves_its_date_annotation(schema):
    """
    The exact regression: ActivityPointOut names its field `date`, which
    shadows the `date` type it is annotated with, so a linter reports the
    import as unused and removing it breaks only the schema.
    """
    activity = schema["components"]["schemas"]["ActivityPointOut"]
    assert activity["properties"]["date"]["format"] == "date"


def test_watch_accepts_an_optional_episode(schema):
    params = schema["paths"]["/api/movies/{movie_id}/watch"]["post"]["parameters"]
    episode = next(p for p in params if p["name"] == "episode_id")
    assert episode["required"] is False, "episode_id must stay optional for older clients"


def test_episode_listing_is_exposed_to_viewers(schema):
    """FR-9: episode and season data used to be reachable only through /api/admin."""
    assert "/api/movies/{movie_id}/episodes" in schema["paths"]
    assert "/api/movies/{movie_id}/seasons" in schema["paths"]


# ---------- documentation exposure ----------
#
# Swagger, ReDoc and the raw schema publish every route, parameter and
# model name in the application. They leak no data — every route still
# demands verified Telegram initData — but in production they hand an
# attacker a map for free, and nothing there consumes them.


def test_production_serves_no_documentation_routes():
    """All three are off together. Leaving the raw schema on would keep
    the map available and only remove the pretty renderer for it."""
    assert docs_urls(production=True) == {
        "openapi_url": None,
        "docs_url": None,
        "redoc_url": None,
    }


def test_development_keeps_the_documentation_routes():
    """Locally they are how the API is read, so the switch must not be
    a one-way removal that costs every developer the tooling."""
    assert docs_urls(production=False) == {
        "openapi_url": "/openapi.json",
        "docs_url": "/docs",
        "redoc_url": "/redoc",
    }


def test_the_schema_is_still_built_in_process_when_the_route_is_gone(schema):
    """
    Turning the route off must not turn the *check* off.

    `app.openapi()` is what forces every response-model annotation to
    resolve, and it is now the only thing standing between a broken model
    and production — so it has to keep working with no documentation
    route mounted at all.
    """
    from fastapi import FastAPI

    silent = FastAPI(title="probe", **docs_urls(production=True))

    @silent.get("/probe")
    def _probe() -> dict[str, str]:
        return {}

    assert silent.openapi()["paths"]["/probe"]
    assert not [route for route in silent.routes if route.path in ("/docs", "/redoc", "/openapi.json")]
    # And the real app's schema is unaffected by any of this.
    assert schema["paths"]


def test_no_api_route_was_removed_by_the_docs_change(schema):
    """
    Documentation exposure is the only thing that changed. Every API
    surface must still be present and reachable.
    """
    for path in (
        "/api/auth/me",
        "/api/movies",
        "/api/admin/broadcasts",
        "/api/admin/broadcasts/estimate",
        "/api/admin/themes",
        "/api/admin/banners",
        "/health",
    ):
        assert path in schema["paths"], f"{path} disappeared"
