"""
Admin video intake.

Forwarding a video into the bot is far easier on a phone than copying a
file_id into a form, so admins drop files here and finish the metadata
in the Mini App. The row this creates is inert — nothing in the catalog
references it until an admin attaches it to an episode, so a stray
forward costs nothing.

Non-admins are ignored silently rather than refused, so the feature
isn't discoverable by sending the bot a random video.
"""
from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.permissions import actor_with_permission
from app.core.permissions import Permission
from app.db.models.content import PendingUpload
from app.db.models.user import User

router = Router(name="admin_upload")


@router.message(F.video | F.document)
async def handle_admin_upload(message: Message, session: AsyncSession, _) -> None:
    if await actor_with_permission(
        session, message.from_user.id, Permission.MANAGE_MOVIES
    ) is None:
        return  # silently ignore — don't reveal this flow to non-admins

    media = message.video or message.document
    if media is None:
        return

    existing = await session.execute(
        select(PendingUpload.id).where(PendingUpload.file_id == media.file_id)
    )
    if existing.scalar_one_or_none() is not None:
        await message.answer(_("admin.upload_duplicate"))
        return

    uploader_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    uploader = uploader_result.scalar_one_or_none()

    session.add(
        PendingUpload(
            file_id=media.file_id,
            uploaded_by_id=uploader.id if uploader else None,
            file_name=getattr(media, "file_name", None),
            file_size=getattr(media, "file_size", None),
            duration_seconds=getattr(media, "duration", None),
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
        )
    )
    await session.flush()

    await message.answer(_("admin.upload_received"))
