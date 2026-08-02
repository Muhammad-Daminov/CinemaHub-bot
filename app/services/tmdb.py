"""
Async TMDB API client.

Every read goes through Redis first — TMDB metadata for a given title
rarely changes within a day, so a cache-aside strategy with a 24h TTL
(7d for the near-static genre map) cuts external calls dramatically
without risking stale posters/ratings for long.
"""
import json
from typing import Any

import aiohttp

from app.core.config import settings
from app.core.redis import get_redis

TMDB_BASE_URL = "https://api.themoviedb.org/3/"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

CACHE_TTL_SECONDS = 60 * 60 * 24        # 24h — search results & details
GENRE_CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7d — genre list is near-static


class TMDBService:
    """Thin async wrapper around TMDB's v3 API with a Redis cache-aside layer."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=TMDB_BASE_URL,
                headers={"Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _cached_get(self, cache_key: str, path: str, params: dict[str, Any], ttl: int) -> dict[str, Any]:
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached is not None:
            return json.loads(cached)

        session = await self._get_session()
        request_params = {"api_key": settings.TMDB_API_KEY, "language": "en-US", **params}
        async with session.get(path, params=request_params) as response:
            response.raise_for_status()
            data = await response.json()

        await redis.set(cache_key, json.dumps(data), ex=ttl)
        return data

    async def search_movie(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        """Returns raw TMDB search result items for a movie title."""
        cache_key = f"tmdb:search:movie:{query.lower()}:{year or ''}"
        params: dict[str, Any] = {"query": query}
        if year:
            params["year"] = year
        data = await self._cached_get(cache_key, "search/movie", params, CACHE_TTL_SECONDS)
        return data.get("results", [])

    async def get_movie_details(self, tmdb_id: int) -> dict[str, Any]:
        """Full details for one movie, including genres."""
        cache_key = f"tmdb:movie:{tmdb_id}"
        return await self._cached_get(cache_key, f"movie/{tmdb_id}", {}, CACHE_TTL_SECONDS)

    async def get_genre_map(self) -> dict[int, str]:
        """{genre_id: genre_name} — used to resolve genre_ids on search results."""
        cache_key = "tmdb:genres:movie"
        data = await self._cached_get(cache_key, "genre/movie/list", {}, GENRE_CACHE_TTL_SECONDS)
        return {g["id"]: g["name"] for g in data.get("genres", [])}

    @staticmethod
    def build_poster_url(poster_path: str | None, size: str = "w500") -> str | None:
        return f"{IMAGE_BASE_URL}/{size}{poster_path}" if poster_path else None


tmdb_service = TMDBService()
