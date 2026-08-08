"""
Viewer-facing money: plans, purchase, top-up.

Everything here runs inside the Mini App. The bot's payment flow still
works, but nothing in this file requires the user to leave the app —
which is the point of Phase 5.

Amounts are Decimal end to end and serialised as float only at the
boundary, matching how balances are stored.
"""
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_active_user
from app.core.i18n import t
from app.db.models.payment import AdminCard, PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.subscription import SubscriptionPlanModel
from app.db.models.user import BalanceHistory, User
from app.db.session import get_db_session
from app.services.images import ImageError, get_image, store_image
from app.services.subscription_plans import list_plans, plan_features
from app.services.subscription_purchase import (
    InsufficientBalanceError,
    PlanUnavailableError,
    active_subscription,
    preview_purchase,
    purchase_plan,
    queued_subscriptions,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class FeatureOut(BaseModel):
    code: str
    name: str
    value: str | None


class PlanOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    price: float
    duration_days: int
    priority: int
    benefits: list[str]
    is_free: bool
    features: list[FeatureOut]


class SubscriptionOut(BaseModel):
    plan_id: int | None
    plan_name: str | None
    started_at: datetime
    expires_at: datetime


class BillingOverviewOut(BaseModel):
    """Everything the subscription screen needs, in one request."""

    balance: float
    plans: list[PlanOut]
    current: SubscriptionOut | None
    # Purchases waiting their turn — a lower tier bought in advance.
    queued: list[SubscriptionOut]


class PurchasePreviewOut(BaseModel):
    outcome: str          # activate | extend | upgrade | queued
    starts_at: datetime
    price: float
    balance: float
    missing: float
    affordable: bool


class InsufficientOut(BaseModel):
    """The numbers the insufficient-balance dialog is required to show."""

    balance: float
    required: float
    missing: float


class CardOut(BaseModel):
    id: int
    card_number: str
    holder_name: str
    bank_name: str | None


class HistoryOut(BaseModel):
    id: int
    amount: float
    kind: str
    description: str | None
    created_at: datetime
    status: str | None


async def _subscription_out(session: AsyncSession, sub) -> SubscriptionOut:
    plan = await session.get(SubscriptionPlanModel, sub.plan_id) if sub.plan_id else None
    return SubscriptionOut(
        plan_id=sub.plan_id,
        plan_name=plan.name if plan else None,
        started_at=sub.started_at,
        expires_at=sub.expires_at,
    )


@router.get("/overview", response_model=BillingOverviewOut)
async def billing_overview(
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_active_user),
) -> BillingOverviewOut:
    """Balance, sellable plans with their features, and what the user holds."""
    plans = await list_plans(session, include_inactive=False)
    out: list[PlanOut] = []
    for plan in plans:
        features = await plan_features(session, plan.id)
        out.append(
            PlanOut(
                id=plan.id,
                code=plan.code,
                name=plan.name,
                description=plan.description,
                price=float(plan.price),
                duration_days=plan.duration_days,
                priority=plan.priority,
                benefits=list(plan.benefits or []),
                is_free=plan.is_free,
                features=[
                    FeatureOut(code=f.code, name=f.name, value=v) for f, v in features
                ],
            )
        )

    current = await active_subscription(session, user.id)
    queued = await queued_subscriptions(session, user.id)
    return BillingOverviewOut(
        balance=float(user.balance),
        plans=out,
        current=await _subscription_out(session, current) if current else None,
        queued=[await _subscription_out(session, q) for q in queued],
    )


@router.get("/plans/{plan_id}/preview", response_model=PurchasePreviewOut)
async def preview(
    plan_id: int,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_active_user),
) -> PurchasePreviewOut:
    """
    What buying this plan would do — computed by the same rules that apply
    it, so the confirmation the user sees cannot disagree with the result.
    """
    try:
        result = await preview_purchase(session, user, plan_id)
    except PlanUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PurchasePreviewOut(
        outcome=result["outcome"],
        starts_at=result["starts_at"],
        price=float(result["price"]),
        balance=float(result["balance"]),
        missing=float(result["missing"]),
        affordable=result["affordable"],
    )


@router.post("/plans/{plan_id}/purchase", response_model=SubscriptionOut)
async def purchase(
    plan_id: int,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_active_user),
) -> SubscriptionOut:
    try:
        subscription = await purchase_plan(session, user, plan_id)
    except PlanUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientBalanceError as exc:
        # 402 with the three numbers the dialog must show, so the client
        # renders them rather than recomputing and risking a mismatch.
        raise HTTPException(
            status_code=402,
            detail={
                "message": t("app.balance_insufficient", user.language),
                "balance": float(exc.balance),
                "required": float(exc.price),
                "missing": float(exc.missing),
            },
        ) from exc
    return await _subscription_out(session, subscription)


# ---------- top-up ----------


@router.get("/cards", response_model=list[CardOut])
async def list_cards(
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(get_active_user),
) -> list[CardOut]:
    result = await session.execute(select(AdminCard).where(AdminCard.is_active.is_(True)))
    return [
        CardOut(
            id=c.id, card_number=c.card_number, holder_name=c.holder_name, bank_name=c.bank_name
        )
        for c in result.scalars()
    ]


@router.post("/topup", response_model=dict)
async def submit_topup(
    amount: float = Form(...),
    card_id: int = Form(...),
    receipt: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_active_user),
) -> dict:
    """
    Submits a top-up receipt uploaded from the device.

    Multipart rather than JSON+base64: the image is already bytes, and
    base64 would inflate it by a third for no benefit. The receipt is
    stored by us, not relayed to Telegram, so the Mini App never has to
    hand the user off to the bot.
    """
    if amount <= 0:
        raise HTTPException(status_code=422, detail=t("app.amount_invalid", user.language))

    card = await session.get(AdminCard, card_id)
    if card is None or not card.is_active:
        raise HTTPException(status_code=404, detail=t("payment.card_not_found", user.language))

    try:
        image = await store_image(session, await receipt.read(), receipt.content_type)
    except ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session.add(
        PaymentReceipt(
            user_id=user.id,
            admin_card_id=card.id,
            purpose=PaymentPurpose.TOPUP,
            amount=Decimal(str(amount)),
            receipt_photo_file_id="",   # Mini App upload: bytes, not a Telegram id
            receipt_image_id=image.id,
            status=PaymentStatus.PENDING,
        )
    )
    await session.flush()
    return {"status": "submitted", "message": t("payment.received", user.language)}


@router.get("/receipts/{receipt_id}/image")
async def get_receipt_image(
    receipt_id: int,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_active_user),
) -> Response:
    """
    Serves a receipt image to the user who submitted it.

    Scoped to the owner: a receipt is a bank document, and a sequential id
    would otherwise let anyone walk the range.
    """
    receipt = await session.get(PaymentReceipt, receipt_id)
    if receipt is None or receipt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    return await _image_response(session, receipt.receipt_image_id, user)


async def _image_response(session: AsyncSession, image_id: int | None, user: User) -> Response:
    if image_id is None:
        raise HTTPException(status_code=404, detail="Not found")
    image = await get_image(session, image_id)
    if image is None or image.data is None:
        # Purged after its retention window. A distinct code lets the client
        # say "no longer stored" rather than "missing".
        raise HTTPException(status_code=410, detail=t("app.image_expired", user.language))
    return Response(
        content=image.data,
        media_type=image.content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/history", response_model=list[HistoryOut])
async def payment_history(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_active_user),
) -> list[HistoryOut]:
    """
    The user's own money movements — ledger entries plus pending receipts.

    Pending receipts are included because they have not moved the balance
    yet, so a user who has just submitted one would otherwise see nothing
    and resubmit.
    """
    ledger = (
        await session.execute(
            select(BalanceHistory)
            .where(BalanceHistory.user_id == user.id)
            .order_by(BalanceHistory.created_at.desc())
            .limit(limit)
        )
    ).scalars()

    rows = [
        HistoryOut(
            id=entry.id,
            amount=float(entry.amount),
            kind=entry.tx_type.value,
            description=entry.description,
            created_at=entry.created_at,
            status=None,
        )
        for entry in ledger
    ]

    pending = (
        await session.execute(
            select(PaymentReceipt)
            .where(
                PaymentReceipt.user_id == user.id,
                PaymentReceipt.status == PaymentStatus.PENDING,
            )
            .order_by(PaymentReceipt.created_at.desc())
        )
    ).scalars()
    rows.extend(
        HistoryOut(
            id=r.id,
            amount=float(r.amount),
            kind="pending_receipt",
            description=None,
            created_at=r.created_at,
            status=r.status.value,
        )
        for r in pending
    )

    rows.sort(key=lambda r: r.created_at, reverse=True)
    return rows[:limit]
