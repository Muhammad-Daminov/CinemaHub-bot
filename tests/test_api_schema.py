"""
OpenAPI schema generation.

Exists because of a real regression: removing an import that a linter
called unused broke schema generation while leaving `import app.main`
perfectly happy. Pydantic resolves response-model annotations lazily, so
a broken one surfaces only when the schema is built — which in practice
meant when someone opened /docs.

`import app.main` cannot catch that class of fault, and neither can a
service-level test. Building the schema does.
"""
import pytest

from app.main import app


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
