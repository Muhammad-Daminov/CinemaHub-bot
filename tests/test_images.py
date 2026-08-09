"""
Uploaded image storage, optimisation and retention.

The retention rule is the one with a promise attached: receipt images go
after 30 days, payment history never does. A sweep that took the history
with it would be worse than no sweep at all.
"""
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import select

from app.db.models.payment import PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.subscription import UploadedImage
from app.services.images import (
    ImageError,
    MAX_UPLOAD_BYTES,
    purge_expired_receipt_images,
    store_image,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


def _png(size=(64, 64), mode="RGB", colour=(255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, colour if mode != "RGBA" else (255, 0, 0, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(size=(64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (0, 128, 255)).save(buffer, format="JPEG")
    return buffer.getvalue()


# ---------- accepting and rejecting ----------


async def test_a_jpeg_is_stored(db_session):
    image = await store_image(db_session, _jpeg(), "image/jpeg")
    assert image.id and image.data
    assert image.content_type == "image/jpeg"
    assert image.byte_size == len(image.data)
    assert (image.width, image.height) == (64, 64)


async def test_webp_is_accepted(db_session):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, format="WEBP")
    image = await store_image(db_session, buffer.getvalue(), "image/webp")
    assert image.data


async def test_transparency_survives_as_png(db_session):
    """Flattening alpha onto JPEG turns a transparent poster into a black box."""
    image = await store_image(db_session, _png(mode="RGBA"), "image/png")
    assert image.content_type == "image/png"


async def test_an_opaque_png_becomes_jpeg(db_session):
    """Photographs are markedly smaller as JPEG, and a receipt is a photograph."""
    image = await store_image(db_session, _png(mode="RGB"), "image/png")
    assert image.content_type == "image/jpeg"


async def test_oversized_images_are_downscaled(db_session):
    image = await store_image(db_session, _jpeg(size=(4000, 3000)), "image/jpeg")
    assert max(image.width, image.height) <= 2000


async def test_a_non_image_is_rejected(db_session):
    with pytest.raises(ImageError):
        await store_image(db_session, b"this is not an image at all", "image/jpeg")


async def test_an_empty_upload_is_rejected(db_session):
    with pytest.raises(ImageError, match="empty"):
        await store_image(db_session, b"", "image/jpeg")


async def test_the_declared_content_type_does_not_decide(db_session):
    """
    Superseded behaviour, kept as an explicit statement of the new rule.

    This used to reject the upload on its *label*, which broke real gallery
    picks: a mobile WebView — which is exactly what a Telegram Mini App is
    — commonly sends `application/octet-stream` or `image/jpg` for a photo
    from the device, and administrators were told "Only JPG, PNG and WEBP
    images are accepted" while holding a perfectly good JPEG.

    A content type is a claim by the client. The bytes are the fact.
    """
    image = await store_image(db_session, _jpeg(), "application/pdf")
    assert image.content_type == "image/jpeg", "the stored type comes from the decode"


@pytest.mark.parametrize(
    "declared",
    ["image/jpeg", "image/jpg", "application/octet-stream", "IMAGE/JPEG", "", None],
    ids=["standard", "non-standard", "webview gallery pick", "uppercase", "empty", "absent"],
)
async def test_a_real_photo_is_accepted_however_it_is_labelled(db_session, declared):
    """Every one of these arrives from a real device; all carry valid JPEG bytes."""
    image = await store_image(db_session, _jpeg(), declared)
    assert image.data


async def test_bytes_that_are_not_an_image_are_still_refused(db_session):
    """The decode is the gate, and it must actually hold."""
    for raw in (b"%PDF-1.4 this is a pdf", b"<svg xmlns='http://www.w3.org/2000/svg'/>"):
        with pytest.raises(ImageError, match="not a readable image"):
            await store_image(db_session, raw, "image/jpeg")


async def test_a_decompression_bomb_is_refused_cleanly(db_session, monkeypatch):
    """
    Pillow guards against a small file that decodes to an enormous bitmap,
    but raises its own error type — which was not caught and escaped as an
    unhandled 500 instead of a refusal the client can read.
    """
    from PIL import Image as PILImage

    def explode(*args, **kwargs):
        raise PILImage.DecompressionBombError("too big")

    monkeypatch.setattr(PILImage, "open", explode)
    with pytest.raises(ImageError, match="too large"):
        await store_image(db_session, _jpeg(), "image/jpeg")


async def test_an_oversized_upload_is_rejected(db_session):
    with pytest.raises(ImageError, match="larger than"):
        await store_image(db_session, b"\x00" * (MAX_UPLOAD_BYTES + 1), "image/jpeg")


async def test_stored_bytes_are_re_encoded_not_passed_through(db_session):
    """
    Re-encoding is what discards EXIF — which carries GPS coordinates on a
    phone photo, a real privacy leak on a payment receipt.
    """
    original = _jpeg(size=(800, 600))
    image = await store_image(db_session, original, "image/jpeg")
    assert image.data != original


# ---------- retention ----------


async def _receipt_with_image(session, user, age_days: int) -> tuple[PaymentReceipt, UploadedImage]:
    image = await store_image(session, _jpeg(), "image/jpeg")
    image.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    receipt = PaymentReceipt(
        user_id=user.id,
        purpose=PaymentPurpose.TOPUP,
        amount=Decimal("50000"),
        receipt_photo_file_id="",
        receipt_image_id=image.id,
        status=PaymentStatus.APPROVED,
    )
    session.add(receipt)
    await session.flush()
    return receipt, image


async def test_receipt_images_older_than_the_window_are_purged(db_session):
    user = await make_user(db_session, 9601)
    _, image = await _receipt_with_image(db_session, user, age_days=31)

    assert await purge_expired_receipt_images(db_session) == 1
    refreshed = await db_session.get(UploadedImage, image.id, populate_existing=True)
    assert refreshed.data is None
    assert refreshed.purged_at is not None
    assert refreshed.byte_size == 0


async def test_recent_receipt_images_are_untouched(db_session):
    user = await make_user(db_session, 9602)
    _, image = await _receipt_with_image(db_session, user, age_days=5)

    assert await purge_expired_receipt_images(db_session) == 0
    refreshed = await db_session.get(UploadedImage, image.id, populate_existing=True)
    assert refreshed.data is not None


async def test_payment_history_survives_the_purge(db_session):
    """The promise: images expire, the money record does not."""
    user = await make_user(db_session, 9603)
    receipt, _ = await _receipt_with_image(db_session, user, age_days=90)

    await purge_expired_receipt_images(db_session)

    stored = await db_session.get(PaymentReceipt, receipt.id, populate_existing=True)
    assert stored is not None
    assert stored.amount == Decimal("50000")
    assert stored.status == PaymentStatus.APPROVED
    assert stored.receipt_image_id is not None, "the reference must still resolve"


async def test_purging_is_idempotent(db_session):
    user = await make_user(db_session, 9604)
    await _receipt_with_image(db_session, user, age_days=40)

    assert await purge_expired_receipt_images(db_session) == 1
    assert await purge_expired_receipt_images(db_session) == 0, (
        "a second sweep must be a no-op, not a repeat write"
    )


async def test_poster_uploads_are_not_purged(db_session):
    """
    Only images a receipt points at expire. A blanket age sweep would take
    every custom poster with it.
    """
    from app.db.models.content import ContentType, Title

    old_poster = await store_image(db_session, _jpeg(), "image/jpeg")
    old_poster.created_at = datetime.now(timezone.utc) - timedelta(days=365)
    title = Title(content_type=ContentType.FILM, name="Old", poster_image_id=old_poster.id)
    db_session.add(title)
    await db_session.flush()

    assert await purge_expired_receipt_images(db_session) == 0
    refreshed = await db_session.get(UploadedImage, old_poster.id, populate_existing=True)
    assert refreshed.data is not None


async def test_no_orphaned_images_after_a_purge(db_session):
    """A purged row must still be reachable from the receipt that referenced it."""
    user = await make_user(db_session, 9605)
    receipt, image = await _receipt_with_image(db_session, user, age_days=45)
    await purge_expired_receipt_images(db_session)

    dangling = (
        await db_session.execute(
            select(PaymentReceipt)
            .outerjoin(UploadedImage, UploadedImage.id == PaymentReceipt.receipt_image_id)
            .where(
                PaymentReceipt.receipt_image_id.is_not(None), UploadedImage.id.is_(None)
            )
        )
    ).scalars().all()
    assert dangling == []
    assert await count_rows(db_session, UploadedImage, id=image.id) == 1
