"""
Rate limiting for the REST API.

The bot has been throttled since the first release; `/api/*` never was.
Every route — including `/billing/topup`, which accepts a multi-megabyte
image and writes to Postgres, and `/movies/{id}/watch`, which makes
Telegram send a video file — was reachable as fast as a client could ask.

Three decisions, each of which is the difference between a limiter and an
outage:

**It fails open.** If Redis is unreachable the request is served and the
failure is logged. A limiter that denies when its own backing store is
down converts a Redis blip into a total API outage, which is strictly
worse than the abuse it prevents.

**It keys on the verified Telegram id where there is one.** The identity
comes from the same `initData` HMAC the auth dependency uses, so a client
cannot pick someone else's bucket — or dodge its own — by editing a
header. Unauthenticated paths fall back to the client IP, which is weaker
(a NAT shares one) but is all that exists before identity is established.

**It counts in a fixed window, not a sliding one.** One `INCR` plus one
`EXPIRE` per request against a per-minute key; the burst edge case at a
window boundary is well understood and cheap, where a sliding window
costs a sorted set and several round trips per request.

Written as raw ASGI rather than subclassing Starlette's
`BaseHTTPMiddleware`. That base class is only reachable by importing
`starlette` directly, which this project gets transitively through
FastAPI and does not declare — precisely the undeclared-dependency shape
that took production down once already (tests/test_runtime_dependencies.py
now fails on it). The ASGI protocol needs no import at all.
"""
import json
import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.auth import verify_init_data
from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60
KEY_PREFIX = "api_rate:"

# Routes whose cost is measured in megabytes or in third-party calls
# rather than in database rows. Matched by prefix on the path, so a path
# parameter in the middle does not need a pattern.
EXPENSIVE_PREFIXES = ("/api/billing/topup",)
# Matched on both ends because the id sits in the middle of this one.
EXPENSIVE_SUFFIXES = ("/watch",)

# Paths that must never be limited. The health check is polled by Render
# and by an uptime monitor; throttling it would take the service down by
# convincing Render it is unhealthy.
EXEMPT_PATHS = ("/health", "/webhook/telegram")


def _is_expensive(path: str) -> bool:
    return path.startswith(EXPENSIVE_PREFIXES) or path.endswith(EXPENSIVE_SUFFIXES)


def _client_identity(request: Request) -> tuple[str, bool]:
    """
    Who to charge this request to, and whether the identity was verified.

    Prefers the Telegram id inside the signed `initData` header. Verifying
    the signature costs one HMAC — cheap, and the alternative (trusting the
    header's contents) would let anyone spend another user's allowance or
    escape their own.
    """
    init_data = request.headers.get("X-Telegram-Init-Data")
    if init_data:
        try:
            fields = verify_init_data(init_data, settings.BOT_TOKEN)
            user_id = json.loads(fields["user"])["id"]
            return f"user:{user_id}", True
        except Exception:  # noqa: BLE001 — an unverifiable header is just an anonymous caller
            pass

    client = request.client
    return f"ip:{client.host if client else 'unknown'}", False


class RateLimitMiddleware:
    """Fixed-window per-caller limiting for /api/*, with a tighter bucket on expensive routes."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        # Lifespan and websocket scopes have no path to limit and must be
        # passed straight through, or startup itself would break.
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope, receive)
        path = scope["path"]
        if path in EXEMPT_PATHS or not path.startswith("/api/"):
            return await self.app(scope, receive, send)

        expensive = _is_expensive(path)
        limit = (
            settings.API_RATE_LIMIT_EXPENSIVE_PER_MINUTE
            if expensive
            else settings.API_RATE_LIMIT_PER_MINUTE
        )
        if limit <= 0:  # 0 disables limiting entirely
            return await self.app(scope, receive, send)

        identity, _verified = _client_identity(request)
        # Expensive routes get their own counter, so a burst of ordinary
        # browsing cannot consume a user's upload allowance or vice versa.
        bucket = "expensive" if expensive else "default"
        window = int(time.time()) // WINDOW_SECONDS
        key = f"{KEY_PREFIX}{bucket}:{identity}:{window}"

        try:
            redis = get_redis()
            count = await redis.incr(key)
            if count == 1:
                # Only on creation: re-setting it every request would slide
                # the window forward and never let the counter reset.
                await redis.expire(key, WINDOW_SECONDS)
        except Exception:  # noqa: BLE001 — see module docstring: fail open
            logger.warning("Rate limiter unavailable, allowing request to %s", path, exc_info=True)
            return await self.app(scope, receive, send)

        if count > limit:
            retry_after = WINDOW_SECONDS - (int(time.time()) % WINDOW_SECONDS)
            logger.info("Rate limit hit by %s on %s (%d/%d)", identity, path, count, limit)
            response = JSONResponse(
                status_code=429,
                # The Mini App renders its own localised message on the 429
                # status; this English detail is for logs and direct API
                # callers, which have no language to render in.
                content={"detail": "Too many requests. Please slow down and try again."},
                headers={"Retry-After": str(retry_after)},
            )
            return await response(scope, receive, send)

        return await self.app(scope, receive, send)
