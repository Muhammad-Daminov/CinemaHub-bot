import asyncio, os, re
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 6427415448

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class UserStates(StatesGroup):
    main = State()
    in_kino_section = State()
    in_serial_section = State()
    viewing_parts = State()

async def db_connect():
    return await asyncpg.connect(DATABASE_URL)

# ================= MENULAR =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🎬 Kinolar"), KeyboardButton(text="📺 Seriallar")]],
        resize_keyboard=True, is_persistent=True
    )

def kino_search_options():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🔎 Nomi orqali")
    kb.button(text="📅 Yili orqali")
    kb.button(text="🔢 Kod orqali")
    kb.button(text="📜 Barcha kinolar")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

# ================= HANDLERLAR =================

@dp.message(F.text == "/start")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.set_state(UserStates.main)
    await m.answer("🎬 Xush kelibsiz! Bo'limni tanlang:", reply_markup=main_menu())

# --- KINO BO'LIMI ---
@dp.message(F.text == "🎬 Kinolar")
async def kino_section(m: types.Message, state: FSMContext):
    await state.set_state(UserStates.in_kino_section)
    await m.answer("Kino qidirish turini tanlang:", reply_markup=kino_search_options())

@dp.message(F.text == "📜 Barcha kinolar", UserStates.in_kino_section)
async def all_movies(m: types.Message):
    conn = await db_connect()
    movies = await conn.fetch("SELECT name, year FROM content WHERE type='kino' ORDER BY id DESC")
    await conn.close()
    
    kb = ReplyKeyboardBuilder()
    for row in movies:
        kb.button(text=f"🎥 {row['name']} ({row['year']})")
    kb.button(text="⬅️ Orqaga")
    kb.adjust(2)
    await m.answer("Hamma kinolar ro'yxati:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.startswith("🎥 "))
async def send_movie(m: types.Message):
    name = re.sub(r'🎥 | \(\d+\)', '', m.text).strip()
    conn = await db_connect()
    res = await conn.fetchrow("SELECT * FROM content WHERE name ILIKE $1 AND type='kino'", name)
    await conn.close()
    if res:
        await m.answer_video(res['file_id'], caption=f"🎬 {res['name']}\n📆 {res['year']}")

# --- SERIAL BO'LIMI ---
@dp.message(F.text == "📺 Seriallar")
async def serial_section(m: types.Message, state: FSMContext):
    await state.set_state(UserStates.in_serial_section)
    conn = await db_connect()
    names = await conn.fetch("SELECT parent_name FROM content WHERE type='part' GROUP BY parent_name ORDER BY MIN(id) ASC")
    await conn.close()
    
    kb = ReplyKeyboardBuilder()
    for row in names:
        kb.button(text=f"📺 {row['parent_name']}")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("📺 Serialni tanlang:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.startswith("📺 "), UserStates.in_serial_section)
async def select_serial(m: types.Message, state: FSMContext):
    ser_name = m.text.replace("📺 ", "")
    await state.update_data(current_ser=ser_name)
    await state.set_state(UserStates.viewing_parts)
    
    conn = await db_connect()
    count = await conn.fetchval("SELECT COUNT(*) FROM content WHERE parent_name=$1", ser_name)
    await conn.close()
    
    kb = ReplyKeyboardBuilder()
    for i in range(1, count + 1, 10):
        end = min(i + 9, count)
        kb.button(text=f"📦 {i}-{end} qismlar")
    kb.button(text="⬅️ Orqaga")
    kb.adjust(2)
    await m.answer(f"🎬 {ser_name} qismlari:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.contains("qismlar"), UserStates.viewing_parts)
async def send_serial_parts(m: types.Message, state: FSMContext):
    data = await state.get_data()
    ser_name = data.get('current_ser')
    nums = re.findall(r'\d+', m.text)
    start, end = int(nums[0]), int(nums[1])
    
    conn = await db_connect()
    parts = await conn.fetch("SELECT file_id, part_number FROM content WHERE parent_name=$1 AND part_number BETWEEN $2 AND $3 ORDER BY part_number ASC", ser_name, start, end)
    await conn.close()
    
    for p in parts:
        await m.answer_video(p['file_id'], caption=f"📺 {ser_name} | {p['part_number']}-qism")
        await asyncio.sleep(0.5)

# --- ORTGA QAYTISH LOGIKASI (MUHIM) ---

@dp.message(F.text == "⬅️ Bosh menyuga")
async def back_to_main(m: types.Message, state: FSMContext):
    await state.set_state(UserStates.main)
    await m.answer("Asosiy menyu:", reply_markup=main_menu())

@dp.message(F.text == "⬅️ Orqaga")
async def smart_back(m: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == UserStates.viewing_parts.state:
        # Serial qismlaridan seriallar ro'yxatiga qaytish
        await serial_section(m, state)
    elif current_state == UserStates.in_kino_section.state:
        # Kino qidiruv turlaridan bosh menyuga
        await back_to_main(m, state)
    else:
        # Agar qayerdaligini bilmasa bosh menyuga yuboramiz
        # Lekin "Barcha kinolar" ichida bo'lsa, kino qidiruv turiga qaytaradi
        await kino_section(m, state)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())