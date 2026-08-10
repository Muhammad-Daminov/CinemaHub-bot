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
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased
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
    TitleTranslation,
    TranslationSource,
    VideoQuality,
)
from app.db.models.payment import (
    AdminCard,
    PaymentPurpose,
    PaymentReceipt,
    PaymentStatus,
    RejectionReason,
)
from app.db.models.promo import PromoCode, PromoDiscountType
from app.db.models.banner import Banner, BannerAudience
from app.db.models.theme import Theme, ThemeAssignment, ThemeScope
from app.db.models.system import (
    Broadcast,
    BroadcastAudience,
    BroadcastMedia,
    BroadcastStatus,
    BroadcastTranslation,
)
from app.db.models.user import SubscriptionPlan, UILanguage, User, UserRole
from app.db.session import AsyncSessionFactory, get_db_session
from app.services.admin_content import admin_content_service
from app.services.banners import (
    ALLOWED_LABEL_KEYS,
    BannerError,
    create_banner,
    list_banners,
    update_banner,
)
from app.services.broadcast import (
    BroadcastError,
    audience_size,
    claim_for_resume,
    create_broadcast,
    estimate_recipients,
    list_broadcasts,
    resumability,
    resume_broadcast,
    run_broadcast,
    set_translations,
)
from app.services.personalization import (
    KNOWN_INTERESTS,
    known_badge_keys,
    known_badge_prefixes,
)
from app.services.images import ImageError, get_image, store_image
from app.services.payment_review import (
    ReceiptNotFoundError,
    ReceiptReviewError,
    approve_receipt,
    flag_amount_mismatch,
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
from app.services.settings_store import get_membership_config, set_membership_config
from app.services.themes import (
    CARD_SHAPES,
    DECORATIONS,
    DEFAULT_TOKENS,
    contrast_warnings,
    ThemeError,
    assign_theme,
    create_theme,
    delete_assignment,
    delete_theme,
    duplicate_theme,
    list_assignments,
    list_themes,
    set_default_theme,
    set_theme_active,
    set_tokens,
)
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
    """Everything a reviewer needs to decide, and to audit the decision later."""

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
    # Which card the user says they paid into — a payment to the wrong
    # destination is one of the built-in rejection reasons, and the
    # reviewer cannot check it without seeing the card.
    card_id: int | None = None
    card_label: str | None = None
    verified_amount: float | None = None
    rejection_reason_id: int | None = None
    reviewed_at: datetime | None = None
    reviewer_telegram_id: int | None = None


class RejectIn(BaseModel):
    notes: str | None = None
    reason_id: int | None = None


class MismatchIn(BaseModel):
    """The figure the reviewer actually read on the receipt."""

    verified_amount: Decimal = Field(gt=0)
    notes: str | None = None


class RejectionReasonOut(BaseModel):
    id: int
    code: str | None
    label: str | None
    sort_order: int


class RejectionReasonIn(BaseModel):
    label: str = Field(min_length=2, max_length=200)
    sort_order: int = 100


@router.get("/receipts", response_model=list[ReceiptOut], dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def list_receipts(
    status: PaymentStatus | None = None,
    q: str | None = Query(default=None, description="Matches username, name or Telegram id"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> list[ReceiptOut]:
    """
    Receipts, newest first. Omitting `status` returns every state — the
    review queue asks for PENDING, the history screen does not.

    Search is server-side because it has to reach receipts beyond the page
    the panel is holding; filtering the loaded list would silently only
    search the most recent fifty.
    """
    filters = []
    if status is not None:
        filters.append(PaymentReceipt.status == status)
    if q:
        needle = q.strip()
        conditions = [User.username.ilike(f"%{needle}%"), User.full_name.ilike(f"%{needle}%")]
        if needle.isdigit():
            conditions.append(User.telegram_id == int(needle))
        filters.append(or_(*conditions))

    reviewer = aliased(User)
    result = await session.execute(
        select(PaymentReceipt, User, AdminCard, reviewer)
        .join(User, User.id == PaymentReceipt.user_id)
        # Outer joins: a receipt predating the card picker has no card, and
        # an unreviewed one has no reviewer. Inner joins would silently
        # drop exactly the rows the queue exists to show.
        .outerjoin(AdminCard, AdminCard.id == PaymentReceipt.admin_card_id)
        .outerjoin(reviewer, reviewer.id == PaymentReceipt.reviewed_by_id)
        .where(*filters)
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
            card_id=card.id if card else None,
            card_label=f"{card.bank_name or card.holder_name} {card.card_number}" if card else None,
            verified_amount=float(receipt.verified_amount) if receipt.verified_amount is not None else None,
            rejection_reason_id=receipt.rejection_reason_id,
            reviewed_at=receipt.reviewed_at,
            reviewer_telegram_id=reviewed_by.telegram_id if reviewed_by else None,
        )
        for receipt, user, card, reviewed_by in result.all()
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
    if body.reason_id is not None and await session.get(RejectionReason, body.reason_id) is None:
        raise HTTPException(status_code=422, detail="Unknown rejection reason")
    try:
        await reject_receipt(session, receipt_id, admin.id, body.notes, body.reason_id)
    except ReceiptNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Receipt not found") from exc
    except ReceiptReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "rejected"}


@router.post("/receipts/{receipt_id}/mismatch", dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))])
async def flag_mismatch_route(
    receipt_id: int,
    body: MismatchIn,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """
    Records that the declared amount disagrees with the receipt.

    Credits nothing — neither figure. Deciding how much someone paid is
    exactly what manual review exists to avoid guessing at. The user is
    told both numbers and can resubmit.
    """
    try:
        await flag_amount_mismatch(session, receipt_id, admin.id, body.verified_amount, body.notes)
    except ReceiptNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Receipt not found") from exc
    except ReceiptReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "mismatch"}


# ---------- Rejection reasons ----------


@router.get(
    "/rejection-reasons",
    response_model=list[RejectionReasonOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))],
)
async def list_rejection_reasons(
    session: AsyncSession = Depends(get_db_session),
) -> list[RejectionReason]:
    result = await session.execute(
        select(RejectionReason)
        .where(RejectionReason.is_active.is_(True))
        .order_by(RejectionReason.sort_order, RejectionReason.id)
    )
    return list(result.scalars())


@router.post(
    "/rejection-reasons",
    response_model=RejectionReasonOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))],
)
async def create_rejection_reason(
    body: RejectionReasonIn, session: AsyncSession = Depends(get_db_session)
) -> RejectionReason:
    """
    Adds a reason.

    No `code`, deliberately: a code implies a locale key, and one an
    administrator invents would resolve to nothing. Admin-authored reasons
    carry their label verbatim and are shown as typed.
    """
    reason = RejectionReason(label=body.label.strip(), sort_order=body.sort_order)
    session.add(reason)
    await session.flush()
    return reason


@router.delete(
    "/rejection-reasons/{reason_id}",
    dependencies=[Depends(require_permission(Permission.MANAGE_PAYMENTS))],
)
async def delete_rejection_reason(
    reason_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    """
    Retires a reason.

    Deactivated, never deleted: receipts point at it, and removing the row
    would erase why a past payment was refused.
    """
    reason = await session.get(RejectionReason, reason_id)
    if reason is None:
        raise HTTPException(status_code=404, detail="Reason not found")
    if reason.code is not None:
        raise HTTPException(status_code=422, detail="Built-in reasons cannot be removed")
    reason.is_active = False
    await session.flush()
    return {"status": "retired"}


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


# ---------- Title translations ----------
#
# Gated on MANAGE_MOVIES rather than MANAGE_LANGUAGES: this edits one
# title's catalog text, and MANAGE_LANGUAGES governs the audio tracks a
# file carries. Someone trusted to rename a title is trusted to name it
# in Russian.


class TitleTranslationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    language: UILanguage
    name: str
    description: str | None
    source: TranslationSource


class TitleTranslationIn(BaseModel):
    language: UILanguage
    # Empty clears the translation for that language — the only way a form
    # whose single affordance is emptying a field can express "remove this".
    name: str = ""
    description: str | None = None


class TitleTranslationsIn(BaseModel):
    translations: list[TitleTranslationIn]


@router.get(
    "/titles/{title_id}/translations",
    response_model=list[TitleTranslationOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))],
)
async def list_title_translations_route(
    title_id: int, session: AsyncSession = Depends(get_db_session)
) -> list[TitleTranslation]:
    return await admin_content_service.list_title_translations(session, title_id)


@router.put(
    "/titles/{title_id}/translations",
    response_model=list[TitleTranslationOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))],
)
async def set_title_translations_route(
    title_id: int,
    body: TitleTranslationsIn,
    session: AsyncSession = Depends(get_db_session),
) -> list[TitleTranslation]:
    """
    Replaces the languages named in the body. Languages the body omits are
    left alone, so editing Russian cannot silently drop English.
    """
    title = await session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")

    if len({entry.language for entry in body.translations}) != len(body.translations):
        raise HTTPException(
            status_code=422, detail="One entry per language — the same language appears twice"
        )

    return await admin_content_service.set_title_translations(
        session,
        title_id,
        {entry.language: (entry.name, entry.description) for entry in body.translations},
    )


@router.post(
    "/titles/{title_id}/translations/tmdb",
    response_model=list[TitleTranslationOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))],
)
async def fill_title_translations_route(
    title_id: int, session: AsyncSession = Depends(get_db_session)
) -> list[TitleTranslation]:
    """
    Pulls Russian and English from TMDB for a title that already has a
    tmdb_id. Separate from enrichment because enrichment refuses to touch
    a manually-overridden title, and that is exactly the title an admin is
    most likely to want translations for.

    Manual translations are preserved — see `fill_translations_from_tmdb`.
    """
    title = await session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    if title.tmdb_id is None:
        raise HTTPException(
            status_code=422, detail="This title has no TMDB match to translate from"
        )
    return await admin_content_service.fill_translations_from_tmdb(session, title)


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


# Declared *after* /titles/similar: FastAPI matches in definition order, so
# a bare {title_id} above it would swallow "similar" and 422.
@router.get(
    "/titles/{title_id}",
    response_model=TitleOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_MOVIES))],
)
async def get_title_route(
    title_id: int, session: AsyncSession = Depends(get_db_session)
) -> Title:
    """
    One title by id.

    Added because the editor had no way to re-read the row it is editing:
    it scanned the first page of the paginated list instead, and once the
    catalog passed 100 titles every older one became invisible to that
    refresh. A poster uploaded against such a title was stored correctly
    and then appeared to revert, because the editor never saw the new
    `poster_image_id` and carried on rendering the TMDB fallback.
    """
    title = await session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return title


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


class BanIn(BaseModel):
    banned: bool


@router.patch(
    "/users/{user_id}/ban",
    response_model=AdminUserOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
async def set_user_ban(
    user_id: int,
    body: BanIn,
    actor: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserOut:
    """
    Blocks or unblocks a user.

    Three refusals, each protecting the authorization model rather than
    the user:

    - The Super Admin can never be banned. The role exists so someone
      always holds every permission; a ban that silenced them would let
      an administrator lock the platform out of itself.
    - Only the Super Admin may ban another administrator. Otherwise two
      admins with MANAGE_USERS could ban each other, and whoever clicked
      first would win a fight the permission system never sanctioned.
    - Nobody bans themselves, which is only ever a misclick.

    A banned administrator keeps their role and permissions: the ban stops
    them *using* the platform, and un-banning restores exactly what they
    had, so the action is reversible.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == actor.id:
        raise HTTPException(status_code=422, detail="You cannot ban yourself")
    if target.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="The Super Admin cannot be banned")
    if target.role == UserRole.ADMIN and actor.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403, detail="Only the Super Admin may ban an administrator"
        )

    target.is_banned = body.banned
    await session.flush()

    premium = await admin_content_service.premium_user_ids(session, [target.id])
    return AdminUserOut(
        id=target.id,
        telegram_id=target.telegram_id,
        username=target.username,
        full_name=target.full_name,
        balance=float(target.balance),
        is_premium=target.id in premium,
        is_banned=target.is_banned,
        created_at=target.created_at,
    )


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


# ---------- Broadcasts ----------
#
# Creating and starting a broadcast is one request. Splitting them would
# leave a PENDING row that an operator could start twice from two tabs,
# and the whole design here is that a broadcast is sent exactly once.


class BroadcastIn(BaseModel):
    # `extra="forbid"` is the security control here, not tidiness. The
    # audience is derived server-side from an enum and an allowlisted
    # target; a request carrying `user_id`, `user_ids` or `recipient_ids`
    # is rejected outright rather than silently ignored, so an attempt to
    # address a broadcast at chosen people fails loudly and visibly.
    model_config = ConfigDict(extra="forbid")

    message: str
    # Per-language bodies, keyed by interface language. The recipient's own
    # language chooses between them at send time — the composing admin's is
    # never consulted — and `message` remains the fallback for anything
    # left blank, so a broadcast is never undeliverable for want of a
    # translation.
    translations: dict[UILanguage, str] | None = None
    audience: BroadcastAudience = BroadcastAudience.ALL
    # What INTEREST/BADGE targets. Validated against the allowlist derived
    # from the badge tables — never interpolated into SQL, never used as a
    # filter expression.
    target_value: str | None = None
    # An allowlisted media kind plus a Telegram file_id captured through
    # the trusted admin forward flow. The pairing is validated server-side;
    # a client-supplied combination is never taken on trust.
    media_type: BroadcastMedia = BroadcastMedia.NONE
    media_file_id: str | None = None


class BroadcastOut(BaseModel):
    id: int
    message: str
    media_type: BroadcastMedia
    audience: BroadcastAudience
    target_value: str | None
    status: BroadcastStatus
    total_recipients: int
    sent_count: int
    failed_count: int
    blocked_count: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class AudienceOut(BaseModel):
    audience: BroadcastAudience
    size: int


def _broadcast_out(row: Broadcast) -> BroadcastOut:
    return BroadcastOut(
        id=row.id,
        message=row.message,
        # The file_id itself is deliberately absent: an admin screen needs
        # to know a broadcast carries a photo, not which one.
        media_type=row.media_type,
        audience=row.audience,
        target_value=row.target_value,
        status=row.status,
        total_recipients=row.total_recipients,
        sent_count=row.sent_count,
        failed_count=row.failed_count,
        blocked_count=row.blocked_count,
        error=row.error,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


@router.get(
    "/broadcasts",
    response_model=list[BroadcastOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def list_broadcasts_route(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> list[BroadcastOut]:
    """History with delivery counts. Deliberately returns no recipient identities."""
    return [_broadcast_out(row) for row in await list_broadcasts(session, limit=limit)]


@router.get(
    "/broadcasts/audience",
    response_model=list[AudienceOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def broadcast_audience_sizes(
    session: AsyncSession = Depends(get_db_session),
) -> list[AudienceOut]:
    """
    How many people each untargeted segment would reach — shown before the
    send, not after.

    INTEREST and BADGE are absent by construction: their size depends on a
    target, so they are quoted through `/broadcasts/estimate` instead.
    Returning a number for "INTEREST" with no target would have to mean
    something, and every available meaning is misleading.
    """
    return [
        AudienceOut(audience=audience, size=await audience_size(session, audience))
        for audience in BroadcastAudience
        if not audience.needs_target
    ]


class TargetOptionsOut(BaseModel):
    """The complete vocabulary of targets. Nothing outside it is accepted."""

    interests: list[str]
    badges: list[str]
    badge_families: list[str]


@router.get(
    "/broadcasts/targets",
    response_model=TargetOptionsOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def broadcast_target_options() -> TargetOptionsOut:
    """
    What an admin may target, served from the same allowlists that validate
    a create request — so the panel cannot offer a choice the API refuses.
    """
    return TargetOptionsOut(
        interests=sorted(KNOWN_INTERESTS),
        badges=sorted(known_badge_keys()),
        badge_families=sorted(known_badge_prefixes()),
    )


class EstimateOut(BaseModel):
    audience: BroadcastAudience
    target_value: str | None
    estimated_recipients: int


@router.get(
    "/broadcasts/estimate",
    response_model=EstimateOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def broadcast_estimate(
    audience: BroadcastAudience,
    target_value: str | None = Query(default=None, max_length=64),
    session: AsyncSession = Depends(get_db_session),
) -> EstimateOut:
    """
    How many people a targeted send would reach, before committing to it.

    Aggregate only. The response model has no field capable of carrying a
    user id, and the count comes from the identical eligibility builder
    materialisation uses — the estimate and the send cannot disagree.
    """
    try:
        count = await estimate_recipients(session, audience, target_value)
    except BroadcastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()  # the freshness pass wrote profiles
    return EstimateOut(
        audience=audience,
        target_value=(target_value or "").strip() or None,
        estimated_recipients=count,
    )


@router.post(
    "/broadcasts",
    response_model=BroadcastOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def create_broadcast_route(
    body: BroadcastIn,
    background: BackgroundTasks,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db_session),
) -> BroadcastOut:
    """
    Records the broadcast and hands the sending to a background task.

    Sending inline would hold the HTTP request — and its database
    transaction — open for the minutes a few thousand messages take, and
    the browser would time out long before the last one went. The task
    opens its own sessions and claims the row under a lock, so a retried
    request cannot produce a second send.
    """
    try:
        broadcast = await create_broadcast(
            session,
            admin,
            body.message,
            body.audience,
            media_type=body.media_type,
            media_file_id=body.media_file_id,
            target_value=body.target_value,
        )
        if body.translations:
            await set_translations(
                session,
                broadcast.id,
                body.translations,
                with_media=body.media_type.needs_file,
            )
    except BroadcastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await session.commit()  # the task runs in its own session and must see this row
    background.add_task(run_broadcast, AsyncSessionFactory, bot, broadcast.id)
    return _broadcast_out(broadcast)


class BroadcastDetailOut(BroadcastOut):
    """
    One broadcast plus its live delivery breakdown.

    Counted from the recipient rows, never from the pre-send estimate:
    progress has to describe what is happening, not what was predicted.
    `can_resume` is the server's decision — the panel renders the button,
    it does not decide whether one is warranted.
    """

    pending: int
    sending: int
    sent: int
    failed: int
    skipped: int
    can_resume: bool
    languages: list[UILanguage]


@router.get(
    "/broadcasts/{broadcast_id}",
    response_model=BroadcastDetailOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def broadcast_detail(
    broadcast_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> BroadcastDetailOut:
    """Authoritative state for the progress screen. Still no recipient identities."""
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise HTTPException(status_code=404, detail="Broadcast not found")

    can_resume, breakdown = await resumability(session, broadcast)
    languages = (
        await session.execute(
            select(BroadcastTranslation.language).where(
                BroadcastTranslation.broadcast_id == broadcast_id
            )
        )
    ).scalars().all()

    return BroadcastDetailOut(
        **_broadcast_out(broadcast).model_dump(),
        pending=breakdown["pending"],
        sending=breakdown["sending"],
        sent=breakdown["sent"],
        failed=breakdown["failed"],
        skipped=breakdown["skipped"],
        can_resume=can_resume,
        languages=sorted(set(languages), key=lambda item: item.value),
    )


@router.post(
    "/broadcasts/{broadcast_id}/resume",
    response_model=BroadcastOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def resume_broadcast_route(
    broadcast_id: int,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> BroadcastOut:
    """
    Restarts a recoverable broadcast against its **existing** recipient rows.

    Resumability is re-checked here under a row lock rather than trusted
    from the panel that offered the button — the screen may have been open
    for an hour. A broadcast that is not recoverable returns 409, which is
    the panel's cue to refresh rather than retry: the state changed under
    it, and repeating the request would not help.

    Nothing is re-materialised, so the audience, the target and the frozen
    recipient set are all untouched.
    """
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise HTTPException(status_code=404, detail="Broadcast not found")

    claimed = await claim_for_resume(session, broadcast_id)
    if claimed is None:
        raise HTTPException(status_code=409, detail="This broadcast cannot be resumed")

    await session.commit()  # the task opens its own session and must see the claim
    background.add_task(resume_broadcast, AsyncSessionFactory, bot, broadcast_id)
    return _broadcast_out(claimed)


# ---------- System settings ----------


class MembershipSettingsOut(BaseModel):
    require_membership: bool
    required_channel: str | None
    # False when a numeric chat id was configured: it works for the check
    # but cannot be turned into a join link, so the panel warns instead of
    # silently showing a prompt with no way to act on it.
    has_invite_url: bool


class MembershipSettingsIn(BaseModel):
    require_membership: bool
    required_channel: str | None = None


def _membership_out(config) -> MembershipSettingsOut:
    return MembershipSettingsOut(
        require_membership=config.enabled,
        required_channel=config.channel,
        has_invite_url=config.invite_url is not None,
    )


@router.get(
    "/settings/membership",
    response_model=MembershipSettingsOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def get_membership_settings(
    session: AsyncSession = Depends(get_db_session),
) -> MembershipSettingsOut:
    return _membership_out(await get_membership_config(session))


@router.put(
    "/settings/membership",
    response_model=MembershipSettingsOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def update_membership_settings(
    body: MembershipSettingsIn,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db_session),
) -> MembershipSettingsOut:
    """
    Sets the required channel.

    Turning the requirement on without naming a channel is refused rather
    than stored: the service treats that combination as "off", and an
    operator who thought they had enabled a gate would never find out.
    """
    channel = (body.required_channel or "").strip() or None
    if body.require_membership and not channel:
        raise HTTPException(
            status_code=422, detail="Name the channel before requiring membership"
        )
    config = await set_membership_config(session, body.require_membership, channel, admin.id)
    return _membership_out(config)


# ---------- Promotional banners ----------
#
# Gated on MANAGE_NOTIFICATIONS: a banner is promotional messaging, the
# same capability that governs broadcasts. Deliberately not a new
# permission — one nobody holds would leave the feature reachable only by
# the Super Admin until every administrator was re-permissioned.


class BannerIn(BaseModel):
    title_id: int | None = None
    headline: str | None = None
    subtitle: str | None = None
    label_key: str | None = None
    image_url: str | None = None
    audience: BannerAudience = BannerAudience.GLOBAL
    target_value: str | None = None
    priority: int = 0
    is_active: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class BannerAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title_id: int | None
    headline: str | None
    subtitle: str | None
    label_key: str | None
    image_url: str | None
    audience: BannerAudience
    target_value: str | None
    priority: int
    is_active: bool
    starts_at: datetime | None
    ends_at: datetime | None


@router.get(
    "/banners",
    response_model=list[BannerAdminOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def list_banners_admin(session: AsyncSession = Depends(get_db_session)) -> list[Banner]:
    """Every campaign, live or not — this screen manages them all."""
    return await list_banners(session)


@router.get(
    "/banners/labels",
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def list_banner_labels() -> dict[str, list[str]]:
    """
    The locale keys a campaign may use.

    Served from the backend so the panel offers exactly what the resolver
    accepts — a free-text field here would either miss the catalog or
    become an injection surface.
    """
    return {"labels": sorted(ALLOWED_LABEL_KEYS)}


@router.post(
    "/banners",
    response_model=BannerAdminOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def create_banner_route(
    body: BannerIn, session: AsyncSession = Depends(get_db_session)
) -> Banner:
    try:
        return await create_banner(session, **body.model_dump())
    except BannerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch(
    "/banners/{banner_id}",
    response_model=BannerAdminOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def update_banner_route(
    banner_id: int, body: BannerIn, session: AsyncSession = Depends(get_db_session)
) -> Banner:
    try:
        banner = await update_banner(session, banner_id, **body.model_dump())
    except BannerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if banner is None:
        raise HTTPException(status_code=404, detail="Banner not found")
    return banner


@router.delete(
    "/banners/{banner_id}",
    dependencies=[Depends(require_permission(Permission.MANAGE_NOTIFICATIONS))],
)
async def delete_banner_route(
    banner_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    banner = await session.get(Banner, banner_id)
    if banner is None:
        raise HTTPException(status_code=404, detail="Banner not found")
    await session.delete(banner)
    await session.flush()
    return {"status": "deleted"}


# ---------- Themes ----------
#
# Gated on MANAGE_SYSTEM_SETTINGS: a theme changes the whole platform's
# appearance, which is a system-wide setting rather than content.


class ThemeIn(BaseModel):
    key: str
    name: str
    description: str | None = None
    tokens: dict[str, str] = {}
    card_shape: str | None = None
    decoration: str | None = None


class ThemeTokensIn(BaseModel):
    tokens: dict[str, str]


class ThemeAdminOut(BaseModel):
    id: int
    key: str
    name: str
    description: str | None
    is_default: bool
    is_active: bool
    tokens: dict[str, str]
    card_shape: str
    decoration: str
    # Advisory readability problems. Never blocking — the admin's colours
    # are not silently changed — but named precisely so the panel can say
    # which pair is unreadable.
    contrast_warnings: list[dict] = []


class ThemeAssignmentIn(BaseModel):
    theme_id: int
    scope: ThemeScope
    user_id: int | None = None
    target_value: str | None = None
    priority: int = 0


class ThemeAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    theme_id: int
    scope: ThemeScope
    user_id: int | None
    target_value: str | None
    priority: int
    is_active: bool


def _theme_out(theme: Theme) -> ThemeAdminOut:
    return ThemeAdminOut(
        id=theme.id,
        key=theme.key,
        name=theme.name,
        description=theme.description,
        is_default=theme.is_default,
        is_active=theme.is_active,
        tokens={token.token: token.value for token in theme.tokens},
        card_shape=theme.card_shape,
        decoration=theme.decoration,
        contrast_warnings=contrast_warnings({t.token: t.value for t in theme.tokens}),
    )


@router.get(
    "/themes",
    response_model=list[ThemeAdminOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def list_themes_route(session: AsyncSession = Depends(get_db_session)) -> list[ThemeAdminOut]:
    return [_theme_out(theme) for theme in await list_themes(session)]


@router.get(
    "/themes/tokens",
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def list_theme_tokens() -> dict[str, object]:
    """
    The token vocabulary and its default values.

    Served from the backend so the builder can only offer what the
    validator accepts — a free-text token name would be refused on save
    and confuse whoever typed it.
    """
    return {
        "defaults": DEFAULT_TOKENS,
        "card_shapes": CARD_SHAPES,
        "decorations": sorted(DECORATIONS),
    }


@router.post(
    "/themes",
    response_model=ThemeAdminOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def create_theme_route(
    body: ThemeIn, session: AsyncSession = Depends(get_db_session)
) -> ThemeAdminOut:
    try:
        theme = await create_theme(
            session,
            key=body.key,
            name=body.name,
            tokens=body.tokens,
            description=body.description,
            card_shape=body.card_shape,
            decoration=body.decoration,
        )
    except ThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _theme_out(theme)


@router.put(
    "/themes/{theme_id}/tokens",
    response_model=ThemeAdminOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def set_theme_tokens_route(
    theme_id: int, body: ThemeTokensIn, session: AsyncSession = Depends(get_db_session)
) -> ThemeAdminOut:
    try:
        theme = await set_tokens(session, theme_id, body.tokens)
    except ThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if theme is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    return _theme_out(theme)


@router.post(
    "/themes/{theme_id}/duplicate",
    response_model=ThemeAdminOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def duplicate_theme_route(
    theme_id: int, body: ThemeIn, session: AsyncSession = Depends(get_db_session)
) -> ThemeAdminOut:
    try:
        theme = await duplicate_theme(session, theme_id, key=body.key, name=body.name)
    except ThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if theme is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    return _theme_out(theme)


@router.post(
    "/themes/{theme_id}/default",
    response_model=ThemeAdminOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def set_default_theme_route(
    theme_id: int, session: AsyncSession = Depends(get_db_session)
) -> ThemeAdminOut:
    try:
        theme = await set_default_theme(session, theme_id)
    except ThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if theme is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    return _theme_out(theme)


@router.patch(
    "/themes/{theme_id}/toggle",
    response_model=ThemeAdminOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def toggle_theme_route(
    theme_id: int, session: AsyncSession = Depends(get_db_session)
) -> ThemeAdminOut:
    theme = await session.get(Theme, theme_id)
    if theme is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    try:
        theme = await set_theme_active(session, theme_id, not theme.is_active)
    except ThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _theme_out(theme)


@router.delete(
    "/themes/{theme_id}",
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def delete_theme_route(
    theme_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    try:
        removed = await delete_theme(session, theme_id)
    except ThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Theme not found")
    return {"status": "deleted"}


@router.get(
    "/theme-assignments",
    response_model=list[ThemeAssignmentOut],
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def list_theme_assignments_route(
    session: AsyncSession = Depends(get_db_session),
) -> list[ThemeAssignment]:
    return await list_assignments(session)


@router.post(
    "/theme-assignments",
    response_model=ThemeAssignmentOut,
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def create_theme_assignment_route(
    body: ThemeAssignmentIn, session: AsyncSession = Depends(get_db_session)
) -> ThemeAssignment:
    try:
        return await assign_theme(
            session,
            body.theme_id,
            body.scope,
            user_id=body.user_id,
            target_value=body.target_value,
            priority=body.priority,
        )
    except ThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete(
    "/theme-assignments/{assignment_id}",
    dependencies=[Depends(require_permission(Permission.MANAGE_SYSTEM_SETTINGS))],
)
async def delete_theme_assignment_route(
    assignment_id: int, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    if not await delete_assignment(session, assignment_id):
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"status": "deleted"}
