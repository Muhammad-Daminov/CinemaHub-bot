"""
Uploaded image storage: validation, optimisation, retention.

Every image the platform accepts — payment receipts, custom posters —
comes through here, so the size ceiling, the format allowlist and the
re-encode happen once rather than at each upload site.

**Why the bytes are re-encoded rather than stored as sent.** An uploaded
file is attacker-controlled input. Decoding it with Pillow and writing
out a fresh image discards everything that is not pixels: EXIF (which
carries GPS coordinates on phone photos — a real privacy leak on a
payment receipt), trailing data, and polyglot files that are valid in two
formats at once. It also bounds the stored size, which matters when the
store is a database column.
"""
import io
import logging
from datetime import datetime, timedelta, timezone

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.subscription import UploadedImage

logger = logging.getLogger(__name__)

# What a browser file picker will offer for "photo". WEBP included per the
# poster requirement; everything is re-encoded anyway, so this list is
# about what we can *decode*, not what we store.
# What we *store*. Not a gate on what may be uploaded — see store_image:
# every upload is re-encoded into one of these regardless of what it
# arrived as, and the decode is what decides whether it is an image.
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

# Generous for a phone photo, mean enough to stop a database column being
# used as a file host.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Long edge. A receipt has to stay legible when zoomed; a poster is shown
# at a few hundred pixels. Neither needs a 12-megapixel original.
MAX_DIMENSION = 2000
JPEG_QUALITY = 85

RECEIPT_RETENTION_DAYS = 30


class ImageError(Exception):
    """Raised when an upload is not a usable image."""


def _optimise(raw: bytes) -> tuple[bytes, str, int, int]:
    """
    Decodes, downscales and re-encodes. Returns (bytes, content_type, w, h).

    Transparency decides the output format: PNG keeps an alpha channel a
    JPEG would flatten onto black, which turns a transparent poster into a
    black rectangle. Everything else becomes JPEG, which is markedly
    smaller for photographs — and a receipt is a photograph.
    """
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Image.DecompressionBombError as exc:
        # Pillow's own guard against a small file that decodes to an
        # enormous bitmap. Its error type is not an OSError, so without
        # this it escaped as an unhandled 500 rather than a clean refusal.
        raise ImageError("That image is too large to process") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError("That file is not a readable image") from exc

    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info

    if max(image.size) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    buffer = io.BytesIO()
    if has_alpha:
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        content_type = "image/png"
    else:
        # Phone photos arrive rotated with an EXIF orientation flag; the
        # flag is dropped by the re-encode, so apply it first or the image
        # is stored sideways.
        image = Image.open(io.BytesIO(raw))
        try:
            from PIL import ImageOps

            image = ImageOps.exif_transpose(image)
        except Exception:  # noqa: BLE001 — orientation is a nicety, not a reason to fail
            pass
        if max(image.size) > MAX_DIMENSION:
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
        image.convert("RGB").save(
            buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True
        )
        content_type = "image/jpeg"

    data = buffer.getvalue()
    return data, content_type, image.size[0], image.size[1]


async def store_image(
    session: AsyncSession, raw: bytes, declared_content_type: str | None = None
) -> UploadedImage:
    """Validates, optimises and persists an upload."""
    if not raw:
        raise ImageError("The uploaded file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImageError(
            f"Image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"
        )
    # The declared content type is a *claim by the client*, and it was
    # being enforced as though it were fact — which rejected real photos.
    # A gallery pick inside a mobile WebView (which is exactly what a
    # Telegram Mini App is) commonly arrives as `application/octet-stream`
    # or `image/jpg`, and an administrator uploading a perfectly good JPEG
    # was told "Only JPG, PNG and WEBP images are accepted".
    #
    # The decode below is the real check, as this module always claimed:
    # Pillow must be able to read the bytes, and everything is re-encoded
    # to JPEG or PNG, which is what actually neutralises a disguised or
    # polyglot file. A type we cannot decode is refused there, with a
    # message about the file rather than about its label.
    if declared_content_type:
        declared = declared_content_type.split(";")[0].strip().lower()
        if declared and declared not in ALLOWED_CONTENT_TYPES:
            logger.info("Upload declared %r; trusting the decoded bytes instead", declared)

    data, content_type, width, height = _optimise(raw)

    image = UploadedImage(
        data=data, content_type=content_type, byte_size=len(data), width=width, height=height
    )
    session.add(image)
    await session.flush()
    return image


async def get_image(session: AsyncSession, image_id: int) -> UploadedImage | None:
    return await session.get(UploadedImage, image_id)


async def purge_expired_receipt_images(
    session: AsyncSession, retention_days: int = RECEIPT_RETENTION_DAYS
) -> int:
    """
    Drops the bytes of receipt images past their retention window.

    Only images actually referenced by a receipt are touched — poster
    uploads have no expiry, and a blanket age sweep would delete them too.

    The row survives with `data = NULL` and `purged_at` set. That is the
    point: **payment history is permanent**, so the receipt keeps its
    amount, status and decision, and its image reference still resolves to
    a row that can say "this was purged" rather than dangling.

    Idempotent — already-purged rows are excluded, so running it twice a
    day costs one indexed query the second time.
    """
    from app.db.models.payment import PaymentReceipt

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    referenced = select(PaymentReceipt.receipt_image_id).where(
        PaymentReceipt.receipt_image_id.is_not(None)
    )
    result = await session.execute(
        update(UploadedImage)
        .where(
            UploadedImage.id.in_(referenced),
            UploadedImage.created_at < cutoff,
            UploadedImage.purged_at.is_(None),
        )
        .values(data=None, byte_size=0, purged_at=datetime.now(timezone.utc))
    )
    purged = result.rowcount or 0
    if purged:
        logger.info("Purged %d receipt image(s) older than %d days", purged, retention_days)
    return purged
