"""
AI Assistant handler.

Two-step flow: tapping the menu button just asks for a mood/genre
description (free, no quota cost) — only the second step, which
actually calls Gemini, is quota-gated. That's why the quota-consuming
handler lives on its own nested `prompt_router` with
AIQuotaMiddleware attached only there, not on the whole `router`.
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.catalog import get_title_card_keyboard
from app.bot.keyboards.main_menu import MENU_AI, menu_texts
from app.bot.middlewares.ai_quota import AIQuotaMiddleware
from app.db.models.user import UILanguage
from app.services.ai import AIServiceError, ai_service

logger = logging.getLogger(__name__)

router = Router(name="ai")
prompt_router = Router(name="ai_prompt")
prompt_router.message.middleware(AIQuotaMiddleware())
router.include_router(prompt_router)


class AIStates(StatesGroup):
    awaiting_prompt = State()


@router.message(F.text.in_(menu_texts(MENU_AI)))
async def handle_ai_entry(message: Message, state: FSMContext, _) -> None:
    await state.set_state(AIStates.awaiting_prompt)
    await message.answer(_("ai.ask_mood"))


@prompt_router.message(AIStates.awaiting_prompt, F.text)
async def handle_ai_prompt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    ai_quota_remaining: int | None,
    lang: UILanguage,
    _,
) -> None:
    await state.clear()

    try:
        result = await ai_service.recommend(session, message.text)
    except AIServiceError as exc:
        logger.error(
            "AI recommendation failed for telegram_id=%s", message.from_user.id, exc_info=True
        )
        if exc.status_code == 429:
            await message.answer(_("ai.overloaded"))
        else:
            await message.answer(_("ai.failed"))
        return

    if not result.titles:
        await message.answer(_("ai.no_match"))
        return

    await message.answer(_("ai.reason", reason=result.reason))
    for title in result.titles:
        caption = _("ai.card", name=title.name, year=title.year or "?")
        keyboard = get_title_card_keyboard(title.id, lang)
        if title.poster_url:
            await message.answer_photo(title.poster_url, caption=caption, reply_markup=keyboard)
        else:
            await message.answer(caption, reply_markup=keyboard)

    if ai_quota_remaining is not None:
        await message.answer(_("ai.quota_left", remaining=ai_quota_remaining))
