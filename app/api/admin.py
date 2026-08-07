"""
Admin REST API. Two gates apply to every route: the router-wide
get_current_admin (verified Telegram initData + an administrator role),
and a per-route require_permission naming the exact capability. The
blanket gate is kept so a newly added route is never accidentally
public even if its author forgets the specific permission. Receipt approval and
promo creation delegate to the same shared services the bot's own
admin commands use (app.services.payment_review, app.services.promo),
so the bot and this dashboard can never apply different business
rules to the same action. Catalog writes go through
app.services.admin_content for the same reason.
"""
from dataclasses import asdict
# `date` looks unused to a linter — the only reference is the annotation in
# ActivityPointOut, where the field is itself named `date` and shadows it.
# Removing it breaks OpenAPI schema generation but NOT import, so it once
# got deleted as dead and shipped. tests/test_api_schema.py now catches that.
from datetime import date, datetime, timedelta, timezone  # noqa: F401  (see above)

import aiohttp
from aiogram.exceptions import TelegramAPIError
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_admin, get_super_admin, require_permission
from app.bot.instance import bot
from app.core.codegen import generate_code
from app.core.permissions import PERMISSION_GROUPS, Permission, parse_permission
from app.db.models.content import (
    AudioLanguage,
    Collection,
    ContentType,
    Episode,
    PendingUpload,
    Title,
    VideoQuality,
)
from app.db.models.payment import AdminCard, PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.promo import PromoCode, PromoDiscountType
from app.db.models.user import SubscriptionPlan, User, UserRole
from app.db.session import get_db_session
from app.services.admin_content import admin_content_service
from app.services.images import ImageError, get_image, store_image
from app.services.payment_review import (
    ReceiptNotFoundError,
    ReceiptReviewError,
    approve_receipt,
    reject_receipt,
)
from app.services.permissions import (
    AdminNotFoundError,
    PermissionError_,
    create_admin,
    list_admins,
    load_permissions,
    remove_admin,
    set_permissions,
)
from app.services.promo import promo_service
from app.services.subscription_plans import (
    PlanError,
    PlanNotFoundError,
    create_feature,
    create_plan,
    delete_feature,
    delete_plan,
    get_plan,
    list_features,
    list_plans,
    plan_features,
    reorder_plans,
    set_plan_features,
    subscriber_count,
    update_plan,
)

router = APIRouter(dependencies=[Depends(get_current_admin)])

# Telegram stores receipt photos as .jpg almost without exception, but
# documents-as-receipts can arrive as png/webp — trust the extension it
# reports rather than guessing a single type for everything.
_IMAGE_MEDIA_TYPES = {
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "heic": "image/heic",
}


def _image_media_type(file_path: str) -> str:
    extension = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return _IMAGE_MEDIA_TYPES.get(extension, "image/jpeg")


# ---------- Stats ----------

class StatsOut(BaseModel):
    total_users: int
    premium_users: int
    total_titles: int
    total_episodes: int
    titles_by_type: dict[str, int]
    pending_receipts: int
    pending_uploads: int
    total_revenue: float
    active_promo_codes: int


class ActivityPointOut(BaseModel):
    date: date
    count: int


class TopUserOut(BaseModel):
    telegram_id: int
    username: str | None
    balance: float


@router.get("/stats", response_model=StatsOut, dependencies=[Depends(require_permission(Permission.VIEW_ANALYTICS))])
async def get_stats(session: AsyncSession = Depends(get_db_session)) -> StatsOut:
    stats = await admin_content_service.dashboard_stats(session)
    return StatsOut(**asdict(stats))


@router.get("/activity", response_model=list[ActivityPointOut], dependencies=[Depends(require_permission(Permission.VIEW_ANALYTICS))])
async def get_activity(session: AsyncSession = Depends(get_db_session)) -> list[dict]:
    return await admin_content_service.activity_last_7_days(session)


@router.get("/top-users", response_model=list[TopUserOut], dependencies=[Depends(require_permission(Permission.VIEW_ANALYTICS))])
async def get_top_users(
    limit: int = Query(default=5, ge=1, le=50), session: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    return await admin_content_service.top_users(session, limit=limit)


# ---------- Payment receipts ----------

class ReceiptOut(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    full_name: str | None
    purpose: PaymentPurpose
    subscription_plan: SubscriptionPlan | None
    amount: float
    receipt_photo_file_id: str
    status: PaymentStatus
    admin_notes: str | None
    created_at: datetime


class RejectIn(BaseModel):
    notes: str


@router.get("/receipts", response_model=list[ReceiptOut], dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def list_receipts(
    status: PaymentStatus = PaymentStatus.PENDING,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> list[ReceiptOut]:
    result = await session.execute(
        select(PaymentReceipt, User)
        .join(User, User.id == PaymentReceipt.user_id)
        .where(PaymentReceipt.status == status)
        .order_by(PaymentReceipt.created_at.desc())
        .limit(limit)
    )
    return [
        ReceiptOut(
            id=receipt.id,
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            purpose=receipt.purpose,
            subscription_plan=receipt.subscription_plan,
            amount=float(receipt.amount),
            receipt_photo_file_id=receipt.receipt_photo_file_id,
            status=receipt.status,
            admin_notes=receipt.admin_notes,
            created_at=receipt.created_at,
        )
        for receipt, user in result.all()
    ]


@router.get("/receipts/{receipt_id}/photo", dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def get_receipt_photo_route(
    receipt_id: int, session: AsyncSession = Depends(get_db_session)
) -> Response:
    """
    Streams the receipt image through the API instead of handing the client
    a Telegram URL — those embed the bot token, and these are user-submitted
    bank documents that must stay behind the router's admin dependency.
    """
    receipt = await session.get(PaymentReceipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    try:
        telegram_file = await bot.get_file(receipt.receipt_photo_file_id)
        if telegram_file.file_path is None:
            raise HTTPException(status_code=502, detail="Telegram returned no file path")
        buffer = await bot.download_file(telegram_file.file_path)
    except TelegramAPIError as exc:
        # Old receipts carry file_ids that Telegram may no longer resolve.
        raise HTTPException(
            status_code=502, detail=f"Telegram could not serve this file: {exc}"
        ) from exc

    if buffer is None:
        raise HTTPException(status_code=502, detail="Telegram returned an empty file")

    return Response(
        content=buffer.read(),
        media_type=_image_media_type(telegram_file.file_path),
        # Private: an admin-only document must never land in a shared cache.
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/receipts/{receipt_id}/approve", dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def approve_receipt_route(
    receipt_id: int, admin: User = Depends(get_current_admin), session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    try:
        await approve_receipt(session, receipt_id, admin.id)
    except ReceiptNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Receipt not found") from exc
    except ReceiptReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "approved"}


@router.post("/receipts/{receipt_id}/reject", dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def reject_receipt_route(
    receipt_id: int,
    body: RejectIn,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    try:
        await reject_receipt(session, receipt_id, admin.id, body.notes)
    except ReceiptNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Receipt not found") from exc
    except ReceiptReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "rejected"}


# ---------- Admin cards ----------

class AdminCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    card_number: str
    holder_name: str
    bank_name: str | None
    is_active: bool


class AdminCardIn(BaseModel):
    card_number: str
    holder_name: str
    bank_name: str | None = None


@router.get("/cards", response_model=list[AdminCardOut], dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def list_cards(session: AsyncSession = Depends(get_db_session)) -> list[AdminCard]:
    result = await session.execute(select(AdminCard).order_by(AdminCard.created_at.desc()))
    return list(result.scalars())


@router.post("/cards", response_model=AdminCardOut, dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def create_card(body: AdminCardIn, session: AsyncSession = Depends(get_db_session)) -> AdminCard:
    card = AdminCard(**body.model_dump())
    session.add(card)
    await session.flush()
    return card


@router.patch("/cards/{card_id}/toggle", response_model=AdminCardOut, dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def toggle_card(card_id: int, session: AsyncSession = Depends(get_db_session)) -> AdminCard:
    card = await session.get(AdminCard, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    card.is_active = not card.is_active
    await session.flush()
    return card


# ---------- Promo codes ----------

class PromoCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    campaign_name: str | None
    discount_type: PromoDiscountType
    value: float
    max_uses: int | None
    current_uses: int
    valid_until: datetime | None
    is_active: bool


class PromoCodeIn(BaseModel):
    discount_type: PromoDiscountType
    value: float
    code: str | None = None  # auto-generated if omitted
    max_uses: int | None = None
    valid_days: int | None = None
    campaign_name: str | None = None


@router.get("/promo", response_model=list[PromoCodeOut], dependencies=[Depends(require_permission(Permission.MANAGE_PROMO_CODES))])
async def list_promo_codes(session: AsyncSession = Depends(get_db_session)) -> list[PromoCode]:
    result = await session.execute(select(PromoCode).order_by(PromoCode.created_at.desc()))
    return list(result.scalars())


@router.post("/promo", response_model=PromoCodeOut, dependencies=[Depends(require_permission(Permission.MANAGE_PROMO_CODES))])
async def create_promo_code_route(
    body: PromoCodeIn, admin: User = Depends(get_current_admin), session: AsyncSession = Depends(get_db_session)
) -> PromoCode:
    valid_until = (
        datetime.now(timezone.utc) + timedelta(days=body.valid_days) if body.valid_days else None
    )
    return await promo_service.create_promo_code(
        session,
        discount_type=body.discount_type,
        value=body.value,
        code=body.code or generate_code(10),
        max_uses=body.max_uses,
        valid_until=valid_until,
        campaign_name=body.campaign_name,
        created_by_id=admin.id,
    )


# ---------- Titles ----------

class TitleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    content_type: ContentType
    name: str
    year: int | None
    genres: list[str] | None
    country: str | None
    tmdb_id: int | None
    poster_url: str | None
    poster_image_id: int | None = None
    description: str | None
    rating: float | None
    view_count: int
    is_active: bool
    is_manual_override: bool
    created_at: datetime


class TitleListItemOut(TitleOut):
    episode_count: int
    file_count: int


class TitlePageOut(BaseModel):
    items: list[TitleListItemOut]
    total: int
    page: int
    page_size: int


class TitleIn(BaseModel):
    name: str
    content_type: ContentType
    year: int | None = None
    genres: list[str] | None = None
    country: str | None = None
    description: str | None = None
    poster_url: str | None = None
    tmdb_id: int | None = None
    rating: float | None = None


class TitleUpdateIn(BaseModel):
    name: str | None = None
    content_type: ContentType | None = None
    year: int | None = None
    genres: list[str] | None = None
    country: str | None = None
    description: str | None = None
    poster_url: str | None = None
    tmdb_id: int | None = None
    rating: float | None = None
    is_active: bool | None = None


@router.get("/titles", response_model=TitlePageOut, dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def list_titles(
    q: str | None = None,
    content_type: ContentType | None = None,
    is_active: bool | None = None,
    page: int = Query(default=0, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> TitlePageOut:
    rows, total = await admin_content_service.list_titles(
        session, query=q, content_type=content_type, is_active=is_active, page=page, page_size=page_size
    )
    return TitlePageOut(
        items=[
            TitleListItemOut(
                **TitleOut.model_validate(title).model_dump(),
                episode_count=episode_count,
                file_count=file_count,
            )
            for title, episode_count, file_count in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/titles", response_model=TitleOut, dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def create_title_route(body: TitleIn, session: AsyncSession = Depends(get_db_session)) -> Title:
    return await admin_content_service.create_title(session, **body.model_dump())


@router.patch("/titles/{title_id}", response_model=TitleOut, dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def update_title_route(
    title_id: int, body: TitleUpdateIn, session: AsyncSession = Depends(get_db_session)
) -> Title:
    fields = body.model_dump(exclude_unset=True)
    title = await admin_content_service.update_title(session, title_id, **fields)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return title


@router.delete("/titles/{title_id}", dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def delete_title_route(
    title_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    if not await admin_content_service.delete_title(session, title_id):
        raise HTTPException(status_code=404, detail="Title not found")
    return {"status": "deleted"}


@router.patch("/titles/{title_id}/toggle", response_model=TitleOut, dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def toggle_title_route(title_id: int, session: AsyncSession = Depends(get_db_session)) -> Title:
    title = await session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    updated = await admin_content_service.set_title_active(session, title_id, not title.is_active)
    return updated


@router.post("/titles/{title_id}/enrich", response_model=TitleOut, dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def enrich_title_route(title_id: int, session: AsyncSession = Depends(get_db_session)) -> Title:
    title = await admin_content_service.enrich_from_tmdb(session, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return title


# ---------- Collections ----------

class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    description: str | None
    poster_url: str | None
    poster_image_id: int | None = None
    sort_order: int
    is_active: bool
    created_at: datetime


class CollectionListItemOut(CollectionOut):
    title_count: int


class CollectionIn(BaseModel):
    name: str
    description: str | None = None
    poster_url: str | None = None
    sort_order: int = 0
    slug: str | None = None


class CollectionUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    poster_url: str | None = None
    sort_order: int | None = None
    slug: str | None = None
    is_active: bool | None = None


class CollectionTitleIn(BaseModel):
    title_id: int


class TitleCollectionsIn(BaseModel):
    collection_ids: list[int]


@router.get("/collections", response_model=list[CollectionListItemOut], dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def list_collections_route(
    session: AsyncSession = Depends(get_db_session),
) -> list[CollectionListItemOut]:
    rows = await admin_content_service.list_collections_admin(session)
    return [
        CollectionListItemOut(
            **CollectionOut.model_validate(collection).model_dump(), title_count=count
        )
        for collection, count in rows
    ]


@router.post("/collections", response_model=CollectionOut, dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def create_collection_route(
    body: CollectionIn, session: AsyncSession = Depends(get_db_session)
) -> Collection:
    return await admin_content_service.create_collection(session, **body.model_dump())


@router.patch("/collections/{collection_id}", response_model=CollectionOut, dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def update_collection_route(
    collection_id: int, body: CollectionUpdateIn, session: AsyncSession = Depends(get_db_session)
) -> Collection:
    collection = await admin_content_service.update_collection(
        session, collection_id, **body.model_dump(exclude_unset=True)
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


@router.patch("/collections/{collection_id}/toggle", response_model=CollectionOut, dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def toggle_collection_route(
    collection_id: int, session: AsyncSession = Depends(get_db_session)
) -> Collection:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return await admin_content_service.set_collection_active(
        session, collection_id, not collection.is_active
    )


@router.delete("/collections/{collection_id}", dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def delete_collection_route(
    collection_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    if not await admin_content_service.delete_collection(session, collection_id):
        raise HTTPException(status_code=404, detail="Collection not found")
    return {"status": "deleted"}


@router.get("/collections/{collection_id}/titles", response_model=list[TitleOut], dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def collection_titles_route(
    collection_id: int, session: AsyncSession = Depends(get_db_session)
) -> list[Title]:
    return await admin_content_service.collection_titles(session, collection_id)


@router.post("/collections/{collection_id}/titles", dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def add_title_to_collection_route(
    collection_id: int, body: CollectionTitleIn, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    if await session.get(Collection, collection_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    if await session.get(Title, body.title_id) is None:
        raise HTTPException(status_code=404, detail="Title not found")
    await admin_content_service.add_title_to_collection(session, collection_id, body.title_id)
    return {"status": "added"}


@router.delete("/collections/{collection_id}/titles/{title_id}", dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def remove_title_from_collection_route(
    collection_id: int, title_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    await admin_content_service.remove_title_from_collection(session, collection_id, title_id)
    return {"status": "removed"}


@router.get("/titles/{title_id}/collections", response_model=list[int], dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def title_collections_route(
    title_id: int, session: AsyncSession = Depends(get_db_session)
) -> list[int]:
    return await admin_content_service.title_collection_ids(session, title_id)


@router.put("/titles/{title_id}/collections", response_model=list[int], dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))])
async def set_title_collections_route(
    title_id: int, body: TitleCollectionsIn, session: AsyncSession = Depends(get_db_session)
) -> list[int]:
    if await session.get(Title, title_id) is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return await admin_content_service.set_title_collections(session, title_id, body.collection_ids)


# ---------- TMDB manual search ----------

class TMDBSearchResultOut(BaseModel):
    id: int
    title: str
    original_title: str | None
    year: int | None
    poster_url: str | None
    overview: str | None


@router.get("/tmdb/search", response_model=list[TMDBSearchResultOut], dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def tmdb_search_route(
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=20),
) -> list[dict]:
    """
    Search TMDB by hand. Auto-enrich matches on the stored Uzbek name and
    misses most of this catalog, so the admin searches the English title
    here and picks the right record themselves. Writes nothing.
    """
    try:
        return await admin_content_service.search_tmdb(q, limit=limit)
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail=f"TMDB request failed: {exc}") from exc


@router.post("/titles/{title_id}/tmdb/{tmdb_id}", response_model=TitleOut, dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def apply_tmdb_match_route(
    title_id: int, tmdb_id: int, session: AsyncSession = Depends(get_db_session)
) -> Title:
    """Applies a chosen TMDB record. Title.name is never overwritten."""
    try:
        title = await admin_content_service.apply_tmdb_match(session, title_id, tmdb_id)
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail=f"TMDB request failed: {exc}") from exc
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return title


# ---------- Duplicate detection ----------

class SimilarTitleOut(BaseModel):
    id: int
    name: str
    content_type: ContentType
    year: int | None
    poster_url: str | None
    episode_count: int
    languages: list[AudioLanguage]


@router.get("/titles/similar", response_model=list[SimilarTitleOut], dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def similar_titles_route(
    name: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
    session: AsyncSession = Depends(get_db_session),
) -> list[SimilarTitleOut]:
    """Existing titles matching `name` — shown while an admin types a new one."""
    rows = await admin_content_service.similar_titles(session, name, limit=limit)
    return [
        SimilarTitleOut(
            id=title.id,
            name=title.name,
            content_type=title.content_type,
            year=title.year,
            poster_url=title.poster_url,
            episode_count=episode_count,
            languages=languages,
        )
        for title, episode_count, languages in rows
    ]


# ---------- Episodes ----------

class EpisodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title_id: int
    season: int
    number: int
    name: str | None
    duration_minutes: int | None
    view_count: int


class EpisodeListItemOut(EpisodeOut):
    file_count: int


class EpisodeIn(BaseModel):
    season: int = 1
    number: int = 1
    name: str | None = None
    duration_minutes: int | None = None


@router.get("/titles/{title_id}/episodes", response_model=list[EpisodeListItemOut], dependencies=[Depends(require_permission(Permission.MANAGE_SERIES))])
async def list_episodes_route(
    title_id: int, session: AsyncSession = Depends(get_db_session)
) -> list[EpisodeListItemOut]:
    rows = await admin_content_service.list_episodes_with_counts(session, title_id)
    return [
        EpisodeListItemOut(**EpisodeOut.model_validate(episode).model_dump(), file_count=file_count)
        for episode, file_count in rows
    ]


@router.post("/titles/{title_id}/episodes", response_model=EpisodeOut, dependencies=[Depends(require_permission(Permission.MANAGE_SERIES))])
async def create_episode_route(
    title_id: int, body: EpisodeIn, session: AsyncSession = Depends(get_db_session)
):
    title = await session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return await admin_content_service.add_episode(session, title_id, **body.model_dump())


@router.delete("/episodes/{episode_id}", dependencies=[Depends(require_permission(Permission.MANAGE_SERIES))])
async def delete_episode_route(
    episode_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    if not await admin_content_service.delete_episode(session, episode_id):
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"status": "deleted"}


# ---------- Media files ----------

class MediaFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    episode_id: int
    file_id: str
    language: AudioLanguage
    quality: VideoQuality
    source_chat_id: int | None
    source_message_id: int | None
    created_at: datetime


class MediaFileIn(BaseModel):
    file_id: str
    language: AudioLanguage = AudioLanguage.UZ_DUB
    quality: VideoQuality = VideoQuality.HD_720
    source_chat_id: int | None = None
    source_message_id: int | None = None


@router.get("/episodes/{episode_id}/files", response_model=list[MediaFileOut], dependencies=[Depends(require_permission(Permission.MANAGE_SERIES))])
async def list_episode_files_route(
    episode_id: int, session: AsyncSession = Depends(get_db_session)
):
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return await admin_content_service.list_files(session, episode_id)


@router.post("/episodes/{episode_id}/files", response_model=MediaFileOut, dependencies=[Depends(require_permission(Permission.MANAGE_SERIES))])
async def attach_file_route(
    episode_id: int, body: MediaFileIn, session: AsyncSession = Depends(get_db_session)
):
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return await admin_content_service.attach_file(session, episode_id, **body.model_dump())


@router.delete("/files/{file_id}", dependencies=[Depends(require_permission(Permission.MANAGE_AUDIO_TRACKS))])
async def detach_file_route(
    file_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    if not await admin_content_service.detach_file(session, file_id):
        raise HTTPException(status_code=404, detail="File not found")
    return {"status": "deleted"}


# ---------- Pending uploads ----------

class PendingUploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_id: str
    uploaded_by_id: int | None
    file_name: str | None
    file_size: int | None
    duration_seconds: int | None
    created_at: datetime


class PendingAttachIn(BaseModel):
    """Attach to an existing title via title_id, or create one by passing name+content_type."""

    title_id: int | None = None
    name: str | None = None
    content_type: ContentType | None = None
    year: int | None = None
    season: int = 1
    number: int = 1
    language: AudioLanguage = AudioLanguage.UZ_DUB
    quality: VideoQuality = VideoQuality.HD_720


@router.get("/pending-uploads", response_model=list[PendingUploadOut], dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def list_pending_uploads_route(
    limit: int = Query(default=100, ge=1, le=200), session: AsyncSession = Depends(get_db_session)
):
    return await admin_content_service.list_pending_uploads(session, limit=limit)


@router.delete("/pending-uploads/{pending_id}", dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def delete_pending_upload_route(
    pending_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    if not await admin_content_service.delete_pending_upload(session, pending_id):
        raise HTTPException(status_code=404, detail="Pending upload not found")
    return {"status": "deleted"}


@router.post("/pending-uploads/{pending_id}/attach", response_model=MediaFileOut, dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))])
async def attach_pending_upload_route(
    pending_id: int, body: PendingAttachIn, session: AsyncSession = Depends(get_db_session)
):
    pending = await session.get(PendingUpload, pending_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="Pending upload not found")

    if body.title_id is not None:
        title = await session.get(Title, body.title_id)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")
    else:
        if not body.name or body.content_type is None:
            raise HTTPException(
                status_code=422, detail="Provide title_id, or name and content_type to create a title"
            )
        title = await admin_content_service.create_title(
            session, name=body.name, content_type=body.content_type, year=body.year
        )

    episode = await admin_content_service.get_or_create_episode(
        session, title.id, body.season, body.number
    )
    media_file = await admin_content_service.attach_file(
        session,
        episode.id,
        file_id=pending.file_id,
        language=body.language,
        quality=body.quality,
        source_chat_id=pending.source_chat_id,
        source_message_id=pending.source_message_id,
    )

    await admin_content_service.delete_pending_upload(session, pending_id)
    return media_file


# ---------- Users ----------

class AdminUserOut(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    full_name: str | None
    balance: float
    is_premium: bool
    is_banned: bool
    created_at: datetime


class UserPageOut(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    page_size: int


@router.get("/users", response_model=UserPageOut, dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
async def list_users_route(
    q: str | None = None,
    page: int = Query(default=0, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> UserPageOut:
    filters = []
    if q:
        filters.append(User.username.ilike(f"%{q.strip()}%"))

    total = (await session.execute(select(func.count(User.id)).where(*filters))).scalar_one()
    result = await session.execute(
        select(User)
        .where(*filters)
        .order_by(User.created_at.desc())
        .offset(page * page_size)
        .limit(page_size)
    )
    users = list(result.scalars())

    # One grouped query for the whole page — never User.is_premium, which
    # would lazy-load .subscriptions per row and raise MissingGreenlet.
    premium_ids = await admin_content_service.premium_user_ids(session, [user.id for user in users])

    items = [
        AdminUserOut(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            balance=float(user.balance),
            is_premium=user.id in premium_ids,
            is_banned=user.is_banned,
            created_at=user.created_at,
        )
        for user in users
    ]
    return UserPageOut(items=items, total=total, page=page, page_size=page_size)


# ---------- Administrators & permissions (Super Admin only) ----------
#
# Gated on get_super_admin rather than a MANAGE_ADMINS permission: if
# appointing administrators were itself a grantable capability, an admin
# holding it could grant themselves everything else, and the single-owner
# model would be a formality. MANAGE_ADMINS/MANAGE_ROLES still exist in
# the vocabulary for read-only surfaces and future delegation.


class AdminPermissionsIn(BaseModel):
    permissions: list[str]


class CreateAdminIn(BaseModel):
    telegram_id: int
    permissions: list[str] = []


class AdminOut(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    full_name: str | None
    role: UserRole
    is_super_admin: bool
    permissions: list[str]


class PermissionCatalogOut(BaseModel):
    """
    The permission vocabulary, grouped for display.

    Served from the backend so the panel cannot show a stale list: a
    capability added to app.core.permissions appears here without a
    frontend release.
    """

    groups: dict[str, list[str]]


def _admin_out(user: User, permissions: set[Permission]) -> AdminOut:
    return AdminOut(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_super_admin=user.role == UserRole.SUPER_ADMIN,
        permissions=sorted(p.value for p in permissions),
    )


def _parse_permissions(raw: list[str]) -> set[Permission]:
    """Rejects the whole request on an unknown name rather than silently dropping it."""
    parsed: set[Permission] = set()
    for name in raw:
        permission = parse_permission(name)
        if permission is None:
            raise HTTPException(status_code=422, detail=f"Unknown permission: {name}")
        parsed.add(permission)
    return parsed


@router.get("/permissions", response_model=PermissionCatalogOut)
async def get_permission_catalog() -> PermissionCatalogOut:
    """Readable by any administrator — it is a vocabulary, not a grant."""
    return PermissionCatalogOut(
        groups={
            group: [p.value for p in permissions]
            for group, permissions in PERMISSION_GROUPS.items()
        }
    )


@router.get("/admins", response_model=list[AdminOut])
async def list_admins_route(
    _actor: User = Depends(get_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminOut]:
    return [_admin_out(user, perms) for user, perms in await list_admins(session)]


@router.post("/admins", response_model=AdminOut)
async def create_admin_route(
    body: CreateAdminIn,
    actor: User = Depends(get_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> AdminOut:
    try:
        user = await create_admin(
            session, actor, body.telegram_id, _parse_permissions(body.permissions)
        )
    except AdminNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError_ as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _admin_out(user, await load_permissions(session, user))


@router.put("/admins/{user_id}/permissions", response_model=AdminOut)
async def set_admin_permissions_route(
    user_id: int,
    body: AdminPermissionsIn,
    actor: User = Depends(get_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> AdminOut:
    try:
        await set_permissions(session, actor, user_id, _parse_permissions(body.permissions))
    except AdminNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError_ as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    user = await session.get(User, user_id)
    return _admin_out(user, await load_permissions(session, user))


@router.delete("/admins/{user_id}")
async def remove_admin_route(
    user_id: int,
    actor: User = Depends(get_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    try:
        await remove_admin(session, actor, user_id)
    except AdminNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError_ as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"status": "removed"}


# ---------- Subscription plans & features ----------
#
# Plans are gated on MANAGE_SUBSCRIPTIONS and the feature catalog on
# MANAGE_SUBSCRIPTION_FEATURES: the two are separate permissions because
# editing a price is a commercial decision, while defining what a feature
# *means* changes what every plan grants.


class PlanFeatureOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    value: str | None


class PlanOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    price: float
    duration_days: int
    benefits: list[str]
    is_active: bool
    is_free: bool
    sort_order: int
    # Surfaced so the panel can explain why a delete is refused before the
    # administrator tries it.
    subscriber_count: int
    features: list[PlanFeatureOut]


class PlanIn(BaseModel):
    code: str
    name: str
    price: float
    duration_days: int
    description: str | None = None
    benefits: list[str] = []
    is_active: bool = True
    is_free: bool = False
    sort_order: int | None = None


class PlanUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = None
    duration_days: int | None = None
    benefits: list[str] | None = None
    is_active: bool | None = None
    is_free: bool | None = None
    sort_order: int | None = None


class ReorderIn(BaseModel):
    plan_ids: list[int]


class FeatureIn(BaseModel):
    code: str
    name: str
    description: str | None = None
    sort_order: int | None = None


class FeatureOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    sort_order: int
    is_active: bool


class PlanFeaturesIn(BaseModel):
    """{feature_id: value}. `value` is null for a plain on/off grant."""

    grants: dict[int, str | None]


async def _plan_out(session: AsyncSession, plan) -> PlanOut:
    features = await plan_features(session, plan.id)
    return PlanOut(
        id=plan.id,
        code=plan.code,
        name=plan.name,
        description=plan.description,
        price=float(plan.price),
        duration_days=plan.duration_days,
        benefits=list(plan.benefits or []),
        is_active=plan.is_active,
        is_free=plan.is_free,
        sort_order=plan.sort_order,
        subscriber_count=await subscriber_count(session, plan.id),
        features=[
            PlanFeatureOut(
                id=feature.id,
                code=feature.code,
                name=feature.name,
                description=feature.description,
                value=value,
            )
            for feature, value in features
        ],
    )


@router.get(
    "/plans",
    response_model=list[PlanOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTIONS))],
)
async def list_plans_route(
    include_inactive: bool = True, session: AsyncSession = Depends(get_db_session)
) -> list[PlanOut]:
    """Defaults to including inactive plans — this is the screen for managing them."""
    plans = await list_plans(session, include_inactive=include_inactive)
    return [await _plan_out(session, plan) for plan in plans]


@router.post(
    "/plans",
    response_model=PlanOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTIONS))],
)
async def create_plan_route(
    body: PlanIn, session: AsyncSession = Depends(get_db_session)
) -> PlanOut:
    try:
        plan = await create_plan(session, **body.model_dump())
    except PlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _plan_out(session, plan)


@router.post(
    "/plans/reorder",
    response_model=list[PlanOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTIONS))],
)
async def reorder_plans_route(
    body: ReorderIn, session: AsyncSession = Depends(get_db_session)
) -> list[PlanOut]:
    try:
        plans = await reorder_plans(session, body.plan_ids)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [await _plan_out(session, plan) for plan in plans]


@router.patch(
    "/plans/{plan_id}",
    response_model=PlanOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTIONS))],
)
async def update_plan_route(
    plan_id: int, body: PlanUpdateIn, session: AsyncSession = Depends(get_db_session)
) -> PlanOut:
    try:
        plan = await update_plan(session, plan_id, **body.model_dump(exclude_unset=True))
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _plan_out(session, plan)


@router.patch(
    "/plans/{plan_id}/toggle",
    response_model=PlanOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTIONS))],
)
async def toggle_plan_route(
    plan_id: int, session: AsyncSession = Depends(get_db_session)
) -> PlanOut:
    try:
        plan = await get_plan(session, plan_id)
        plan = await update_plan(session, plan_id, is_active=not plan.is_active)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _plan_out(session, plan)


@router.delete(
    "/plans/{plan_id}",
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTIONS))],
)
async def delete_plan_route(
    plan_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    try:
        await delete_plan(session, plan_id)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanError as exc:
        # 409, not 422: the request is valid, the plan's state forbids it.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.put(
    "/plans/{plan_id}/features",
    response_model=PlanOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTIONS))],
)
async def set_plan_features_route(
    plan_id: int, body: PlanFeaturesIn, session: AsyncSession = Depends(get_db_session)
) -> PlanOut:
    try:
        await set_plan_features(session, plan_id, body.grants)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _plan_out(session, await get_plan(session, plan_id))


@router.get(
    "/features",
    response_model=list[FeatureOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTION_FEATURES))],
)
async def list_features_route(
    session: AsyncSession = Depends(get_db_session),
) -> list[FeatureOut]:
    return [
        FeatureOut(
            id=f.id, code=f.code, name=f.name, description=f.description,
            sort_order=f.sort_order, is_active=f.is_active,
        )
        for f in await list_features(session, include_inactive=True)
    ]


@router.post(
    "/features",
    response_model=FeatureOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTION_FEATURES))],
)
async def create_feature_route(
    body: FeatureIn, session: AsyncSession = Depends(get_db_session)
) -> FeatureOut:
    try:
        feature = await create_feature(session, **body.model_dump())
    except PlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FeatureOut(
        id=feature.id, code=feature.code, name=feature.name,
        description=feature.description, sort_order=feature.sort_order,
        is_active=feature.is_active,
    )


@router.delete(
    "/features/{feature_id}",
    dependencies=[Depends(require_permission(Permission.MANAGE_SUBSCRIPTION_FEATURES))],
)
async def delete_feature_route(
    feature_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    try:
        await delete_feature(session, feature_id)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}


# ---------- Receipt history, image serving, poster uploads ----------


class ReceiptHistoryOut(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    full_name: str | None
    purpose: PaymentPurpose
    amount: float
    status: PaymentStatus
    admin_notes: str | None
    created_at: datetime
    reviewed_at: datetime | None
    # Which endpoint serves the image, or null when there is none to serve.
    has_image: bool


@router.get(
    "/receipts/history",
    response_model=list[ReceiptHistoryOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))],
)
async def receipt_history(
    status: PaymentStatus | None = None,
    q: str | None = Query(default=None, description="Matches username, full name or Telegram id"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[ReceiptHistoryOut]:
    """
    Every receipt, filterable by status and searchable by user.

    Separate from `/receipts`, which is the pending review queue. History
    is permanent and includes receipts whose image has since been purged —
    the money record outlives the evidence.
    """
    stmt = (
        select(PaymentReceipt, User)
        .join(User, User.id == PaymentReceipt.user_id)
        .order_by(PaymentReceipt.created_at.desc())
    )
    if status is not None:
        stmt = stmt.where(PaymentReceipt.status == status)
    if q:
        needle = f"%{q.strip()}%"
        conditions = [User.username.ilike(needle), User.full_name.ilike(needle)]
        # A bare number is almost certainly a Telegram id; matching it as
        # text as well means the same box serves both kinds of search.
        if q.strip().isdigit():
            conditions.append(User.telegram_id == int(q.strip()))
        stmt = stmt.where(or_(*conditions))

    result = await session.execute(stmt.offset(offset).limit(limit))
    return [
        ReceiptHistoryOut(
            id=r.id,
            telegram_id=u.telegram_id,
            username=u.username,
            full_name=u.full_name,
            purpose=r.purpose,
            amount=float(r.amount),
            status=r.status,
            admin_notes=r.admin_notes,
            created_at=r.created_at,
            reviewed_at=r.reviewed_at,
            has_image=bool(r.receipt_image_id or r.receipt_photo_file_id),
        )
        for r, u in result.all()
    ]


@router.get(
    "/receipts/{receipt_id}/image",
    dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))],
)
async def admin_receipt_image(
    receipt_id: int, session: AsyncSession = Depends(get_db_session)
) -> Response:
    """
    Serves a Mini-App-uploaded receipt.

    Distinct from `/receipts/{id}/photo`, which proxies a Telegram-hosted
    one. Both exist because receipts submitted before Phase 5 have no
    bytes of ours to serve.
    """
    receipt = await session.get(PaymentReceipt, receipt_id)
    if receipt is None or receipt.receipt_image_id is None:
        raise HTTPException(status_code=404, detail="No stored image for this receipt")

    image = await get_image(session, receipt.receipt_image_id)
    if image is None or image.data is None:
        raise HTTPException(status_code=410, detail="Image removed after its retention period")
    return Response(
        content=image.data,
        media_type=image.content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post(
    "/titles/{title_id}/poster",
    dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))],
)
async def upload_title_poster(
    title_id: int,
    poster: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Custom poster. Replaces any previous upload; TMDB's URL is kept as the fallback."""
    title = await session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    try:
        image = await store_image(session, await poster.read(), poster.content_type)
    except ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    title.poster_image_id = image.id
    await session.flush()
    return {"status": "uploaded", "image_id": image.id}


@router.delete(
    "/titles/{title_id}/poster",
    dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))],
)
async def clear_title_poster(
    title_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Drops the custom poster, falling back to whatever TMDB supplied."""
    title = await session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    title.poster_image_id = None
    await session.flush()
    return {"status": "cleared"}


@router.post(
    "/collections/{collection_id}/poster",
    dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))],
)
async def upload_collection_poster(
    collection_id: int,
    poster: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    try:
        image = await store_image(session, await poster.read(), poster.content_type)
    except ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    collection.poster_image_id = image.id
    await session.flush()
    return {"status": "uploaded", "image_id": image.id}


@router.delete(
    "/collections/{collection_id}/poster",
    dependencies=[Depends(require_permission(Permission.MANAGE_CATEGORIES))],
)
async def clear_collection_poster(
    collection_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    collection.poster_image_id = None
    await session.flush()
    return {"status": "cleared"}
