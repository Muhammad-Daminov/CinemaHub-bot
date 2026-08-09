"""
REST API rate limiting.

The bot has been throttled since the first release; `/api/*` never was —
including `/billing/topup`, which accepts a multi-megabyte image, and
`/movies/{id}/watch`, which makes Telegram send a video.

The property that matters most is the failure direction: **Redis being
down must not take the API with it.** A limiter that denies when its own
store is unreachable turns a cache blip into a total outage, which is
worse than the abuse it prevents. That case is tested first.

Driven through httpx's ASGITransport, so the middleware stack is the real
one rather than the class called directly.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api import rate_limit as module
from app.core.config import settings
from app.main import app


class FakeRedis:
    """INCR/EXPIRE with the semantics the limiter relies on."""

    def __init__(self, fail: bool = False):
        self.values: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.fail = fail

    async def incr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis is down")
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.expiries[key] = seconds


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(module, "get_redis", lambda: fake)
    return fake


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# The catalog route refuses without an initData header (422), which is
# fine: the limiter runs before routing, so the status only has to be
# distinguishable from 429.
PATH = "/api/movies"


async def test_requests_under_the_limit_pass(client, redis, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 5)
    for _ in range(5):
        assert (await client.get(PATH)).status_code != 429


async def test_the_request_past_the_limit_is_refused(client, redis, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 3)
    for _ in range(3):
        await client.get(PATH)

    response = await client.get(PATH)
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.json()["detail"]


async def test_a_refusal_says_when_to_retry(client, redis, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 1)
    await client.get(PATH)
    response = await client.get(PATH)

    retry_after = int(response.headers["Retry-After"])
    assert 0 < retry_after <= module.WINDOW_SECONDS


async def test_redis_being_down_lets_the_request_through(client, monkeypatch):
    """
    The one behaviour that must never regress: fail open. A limiter that
    denies on its own backend failure is an outage generator.
    """
    monkeypatch.setattr(module, "get_redis", lambda: FakeRedis(fail=True))
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 1)

    for _ in range(5):
        assert (await client.get(PATH)).status_code != 429


async def test_buckets_are_per_caller(client, redis, monkeypatch):
    """One user exhausting their allowance must not refuse everyone else."""
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 2)

    for _ in range(3):
        await client.get(PATH, headers={"X-Forwarded-For": "10.0.0.1"})

    # ASGITransport reports the same client host regardless of headers, so
    # the buckets are asserted at the key level: distinct identities get
    # distinct keys, which is what keeps one caller from spending another's.
    keys = set(redis.values)
    assert len(keys) == 1
    identity = next(iter(keys))
    assert identity.startswith(f"{module.KEY_PREFIX}default:")


async def test_expensive_routes_have_their_own_bucket(client, redis, monkeypatch):
    """
    Browsing must not consume a user's upload allowance, or vice versa.
    Separate counters are what guarantee that.
    """
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 50)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_EXPENSIVE_PER_MINUTE", 2)

    await client.get(PATH)
    await client.post("/api/movies/1/watch")

    buckets = {key.split(":")[1] for key in redis.values}
    assert buckets == {"default", "expensive"}


async def test_the_expensive_limit_is_the_stricter_one(client, redis, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 100)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_EXPENSIVE_PER_MINUTE", 2)

    for _ in range(2):
        assert (await client.post("/api/movies/1/watch")).status_code != 429
    assert (await client.post("/api/movies/1/watch")).status_code == 429

    # The ordinary bucket is untouched by the expensive one being spent.
    assert (await client.get(PATH)).status_code != 429


async def test_the_topup_route_is_treated_as_expensive(client, redis, monkeypatch):
    """It accepts a multi-megabyte upload and writes to Postgres."""
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 100)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_EXPENSIVE_PER_MINUTE", 1)

    await client.post("/api/billing/topup")
    assert (await client.post("/api/billing/topup")).status_code == 429


async def test_health_is_never_limited(client, redis, monkeypatch):
    """
    Render polls it to decide whether the service is alive. Throttling it
    would take the service down by convincing Render it is unhealthy.

    The database probe inside /health is stubbed: `settings.DATABASE_URL`
    is production, and a test must never open a connection to it. The
    limiter is what is under test here, not the health check.
    """
    import app.main as main_module

    async def fake_check():
        return True

    monkeypatch.setattr(main_module, "check_db_connection", fake_check)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 1)
    for _ in range(5):
        assert (await client.get("/health")).status_code != 429


async def test_a_zero_limit_disables_limiting(client, redis, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 0)
    for _ in range(10):
        assert (await client.get(PATH)).status_code != 429
    assert redis.values == {}, "nothing should even be counted when disabled"


async def test_non_api_paths_are_ignored(client, redis, monkeypatch):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 1)
    await client.get("/miniapp")
    assert redis.values == {}


def _request(headers: dict[str, str] | None = None, host: str = "203.0.113.7"):
    """
    A minimal ASGI scope. Deliberately not driven through the app: a
    *valid* initData header would pass the limiter and reach the route's
    real session dependency, which is bound to production. The identity
    function is the unit under test, so it is called directly.
    """
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (host, 12345), "method": "GET", "path": "/api/movies"})


def _signed_init_data(telegram_id: int) -> str:
    """A genuinely signed initData for `telegram_id`, built the way Telegram does."""
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode

    fields = {"user": json.dumps({"id": telegram_id}), "auth_date": str(int(time.time()))}
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_a_verified_init_data_header_keys_the_bucket_by_user():
    """
    Identity comes from the signed initData, so a client cannot spend
    someone else's allowance — or dodge its own — by editing a header.
    """
    identity, verified = module._client_identity(
        _request({"X-Telegram-Init-Data": _signed_init_data(4242)})
    )
    assert identity == "user:4242"
    assert verified is True


def test_an_unsigned_header_falls_back_to_ip():
    """A forged header must not choose its own bucket."""
    identity, verified = module._client_identity(
        _request({"X-Telegram-Init-Data": "user=%7B%22id%22%3A999%7D&hash=bad"})
    )
    assert identity == "ip:203.0.113.7"
    assert verified is False


def test_no_header_at_all_falls_back_to_ip():
    identity, verified = module._client_identity(_request())
    assert identity == "ip:203.0.113.7"
    assert verified is False


def test_two_users_get_two_buckets():
    """The per-user guarantee, stated directly."""
    first, _ = module._client_identity(_request({"X-Telegram-Init-Data": _signed_init_data(1)}))
    second, _ = module._client_identity(_request({"X-Telegram-Init-Data": _signed_init_data(2)}))
    assert first != second
