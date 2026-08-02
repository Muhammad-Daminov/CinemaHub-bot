"""
AI movie recommendation service, backed by Gemini's structured-output
mode.

Rather than letting the model invent titles, we hand it a compact
slice of our own catalog (id/title/year/genres) and constrain the
response schema to `movie_ids` drawn from that list — then filter the
result against the same id set again on our side, so a hallucinated
id can never surface as a "recommendation". Plain aiohttp (no Google
SDK) to keep this consistent with the tmdb.py client and avoid an
extra heavy dependency.
"""
import json
import logging
from dataclasses import dataclass

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.content import Episode, MediaFile, Title

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/"

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "movie_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "reason": {"type": "STRING"},
    },
    "required": ["movie_ids", "reason"],
}

SYSTEM_INSTRUCTION = (
    "You are a movie recommendation assistant for an Uzbek movie streaming bot. "
    "You will be given the user's mood/description and a numbered catalog of "
    "available movies. Pick up to 5 movies from the catalog that best match the "
    "request. You MUST only use ids that appear in the catalog — never invent an "
    "id or a title. Reply with the required JSON only. Keep `reason` short and "
    "written in the same language as the user's request."
)


class AIServiceError(Exception):
    """
    Raised on any Gemini call/parse failure.

    Carries the HTTP status when the failure came from the API itself, so
    the handler can tell a rate limit (429) apart from a real outage and
    say something accurate to the user. None for parse-side failures.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AIRecommendationResult:
    titles: list[Title]
    reason: str


class AIService:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(base_url=GEMINI_BASE_URL)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def recommend(self, session: AsyncSession, user_prompt: str) -> AIRecommendationResult:
        candidates = await self._fetch_candidate_catalog(session)
        if not candidates:
            raise AIServiceError("Catalog is empty — nothing to recommend from.")

        catalog_text = "\n".join(
            f"{t.id}: {t.name} ({t.year or '?'}) [{t.content_type.value}] - {', '.join(t.genres or [])}"
            for t in candidates
        )
        raw_text = await self._call_gemini(user_prompt, catalog_text)

        try:
            parsed = json.loads(raw_text)
            requested_ids = [int(i) for i in parsed["movie_ids"]]
            reason = str(parsed["reason"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise AIServiceError("Gemini returned an unparseable response") from exc

        valid_ids = {t.id for t in candidates}
        by_id = {t.id: t for t in candidates}
        safe_titles = [by_id[i] for i in requested_ids if i in valid_ids]

        return AIRecommendationResult(titles=safe_titles, reason=reason)

    async def _fetch_candidate_catalog(self, session: AsyncSession) -> list[Title]:
        # EXISTS rather than a relationship walk: this runs under async
        # SQLAlchemy, where touching Title.episodes lazily raises
        # MissingGreenlet — and it keeps the whole thing to one query.
        has_playable_file = (
            select(MediaFile.id)
            .join(Episode, Episode.id == MediaFile.episode_id)
            .where(Episode.title_id == Title.id)
            .exists()
        )
        result = await session.execute(
            select(Title)
            .where(Title.is_active.is_(True), has_playable_file)
            .order_by(Title.view_count.desc())
            .limit(settings.AI_CATALOG_CONTEXT_LIMIT)
        )
        return list(result.scalars())

    async def _call_gemini(self, user_prompt: str, catalog_text: str) -> str:
        http_session = await self._get_session()
        url = f"models/{settings.GEMINI_MODEL}:generateContent"
        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [
                {"role": "user", "parts": [{"text": f"User request: {user_prompt}\n\nCatalog:\n{catalog_text}"}]}
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": RESPONSE_SCHEMA,
                "temperature": 0.6,
                "max_output_tokens": 500,
            },
        }
        params = {"key": settings.GEMINI_API_KEY}

        async with http_session.post(url, params=params, json=body) as response:
            if response.status != 200:
                # Google puts the actual cause (quota exhausted, bad key,
                # model not found) in the body — without it the log says
                # nothing and the failure has to be reproduced by hand.
                error_body = await response.text()
                logger.error(
                    "Gemini API error: HTTP %s — %s", response.status, error_body
                )
                raise AIServiceError(
                    f"Gemini API error: HTTP {response.status} — {error_body}",
                    status_code=response.status,
                )
            data = await response.json()

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise AIServiceError("Gemini response missing expected content") from exc


ai_service = AIService()
