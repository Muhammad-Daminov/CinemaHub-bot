import asyncio
import os
import sys
import asyncpg
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from dotenv import load_dotenv

# 1. Sozlamalar
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 6427415448
CHANNEL_ID = "@cinemahubb_HD"

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# 2. Holatlar (States)
class UserStates(StatesGroup):
    waiting_for_search_query = State()

class AdminStates(StatesGroup):
    waiting_for_kino_table = State()
    waiting_for_kino_video = State()
    waiting_for_serial_name = State()
    waiting_for_serial_video = State()
    waiting_for_edit_data = State()
    waiting_for_broadcast = State()

# ================= DB QISMI =================
async def db_connect():
    return await asyncpg.connect(DATABASE_URL)

async def create_table():
    conn = await db_connect()
    # Foydalanuvchilar jadvali reklama uchun kerak
    await conn.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY)")
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS content (
        id SERIAL PRIMARY KEY,
        type TEXT,
        name TEXT,
        year TEXT,
        genre TEXT,
        lang TEXT,
        country TEXT,
        file_id TEXT,
        part_number INT DEFAULT NULL,
        parent_name TEXT DEFAULT NULL
    )
    """)
    await conn.close()

# ================= MENULAR (REPLY) =================
def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎬 Kinolar"), KeyboardButton(text="📺 Seriallar")]
    ], resize_keyboard=True)

def kino_search_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🔎 Nomi orqali"), KeyboardButton(text="📅 Yili orqali")],
        [KeyboardButton(text="🎭 Janr orqali"), KeyboardButton(text="🔢 Kod orqali")],
        [KeyboardButton(text="📜 Barcha kinolar"), KeyboardButton(text="⬅️ Ortga")]
    ], resize_keyboard=True)

# ================= QISMLARNI GURUHLASH MANTIGI =================
def get_parts_group_menu(total_parts):
    kb = ReplyKeyboardBuilder()
    for i in range(1, total_parts + 1, 10):
        end = min(i + 9, total_parts)
        kb.button(text=f"📦 {i}-{end} qismlar")
    kb.button(text="⬅️ Seriallarga qaytish")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

# ================= START & SUB =================
@dp.message(F.text == "/start")
async def start_cmd(m: types.Message):
    conn = await db_connect()
    await conn.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await conn.close()
    
    await m.answer("🎬 Xush kelibsiz! Bo'limni tanlang:", reply_markup=main_menu())

# ================= KINO VA QIDIRUV =================
@dp.message(F.text == "🎬 Kinolar")
async def kino_section(m: types.Message):
    await m.answer("Kino qidirish turini tanlang:", reply_markup=kino_search_menu())

@dp.message(F.text == "📜 Barcha kinolar")
async def all_movies(m: types.Message):
    conn = await db_connect()
    movies = await conn.fetch("SELECT id, name, year FROM content WHERE type='kino' ORDER BY id DESC")
    await conn.close()
    if not movies: return await m.answer("Kinolar yo'q")
    
    kb = InlineKeyboardBuilder()
    for row in movies:
        kb.button(text=f"{row['name']} ({row['year']})", callback_data=f"view_{row['id']}")
    kb.adjust(1)
    await m.answer("📜 Barcha kinolar:", reply_markup=kb.as_markup())

@dp.message(F.text.in_(["🔎 Nomi orqali", "📅 Yili orqali", "🎭 Janr orqali", "🔢 Kod orqali"]))
async def ask_search(m: types.Message, state: FSMContext):
    await m.answer(f"{m.text} bo'yicha qidiruv so'zini yuboring:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(UserStates.waiting_for_search_query)

@dp.message(UserStates.waiting_for_search_query)
async def process_search(m: types.Message, state: FSMContext):
    query = m.text.strip()
    conn = await db_connect()
    
    if query.isdigit() and len(query) < 4: # Kod orqali
        res = await conn.fetch("SELECT * FROM content WHERE id=$1", int(query))
    elif query.isdigit() and len(query) == 4: # Yil orqali
        res = await conn.fetch("SELECT * FROM content WHERE year=$1 AND type='kino'", query)
    else: # Nomi yoki Janr
        res = await conn.fetch("SELECT * FROM content WHERE (name ILIKE $1 OR genre ILIKE $1) AND type='kino'", f"%{query}%")
    
    await conn.close()
    if not res:
        await m.answer("❌ Topilmadi.", reply_markup=main_menu())
    else:
        kb = InlineKeyboardBuilder()
        for row in res:
            kb.button(text=f"{row['name'] or row['parent_name']}", callback_data=f"view_{row['id']}")
        kb.adjust(1)
        await m.answer("Natijalar:", reply_markup=kb.as_markup())
    await state.clear()

# ================= SERIAL BO'LIMI (TARTIBLANGAN) =================
@dp.message(F.text == "📺 Seriallar")
@dp.message(F.text == "⬅️ Seriallarga qaytish")
async def serial_list(m: types.Message):
    conn = await db_connect()
    names = await conn.fetch("SELECT parent_name FROM content WHERE type='part' GROUP BY parent_name ORDER BY MIN(id) ASC")
    await conn.close()
    
    kb = ReplyKeyboardBuilder()
    for row in names:
        kb.button(text=f"📺 {row['parent_name']}")
    kb.button(text="⬅️ Ortga")
    kb.adjust(1)
    await m.answer("📺 Serialni tanlang:", reply_markup=kb.as_markup())

@dp.message(F.text.startswith("📺 "))
async def select_serial(m: types.Message, state: FSMContext):
    ser_name = m.text.replace("📺 ", "")
    conn = await db_connect()
    count = await conn.fetchval("SELECT COUNT(*) FROM content WHERE parent_name=$1", ser_name)
    await conn.close()
    
    await state.update_data(current_ser=ser_name)
    await m.answer(f"🎬 {ser_name} tanlandi. Qismlarni tanlang:", reply_markup=get_parts_group_menu(count))

@dp.message(F.text.contains("qismlar"))
async def show_grouped_parts(m: types.Message, state: FSMContext):
    data = await state.get_data()
    ser_name = data.get('current_ser')
    nums = re.findall(r'\d+', m.text)
    start, end = int(nums[0]), int(nums[1])
    
    conn = await db_connect()
    parts = await conn.fetch("SELECT id, part_number FROM content WHERE parent_name=$1 AND part_number BETWEEN $2 AND $3 ORDER BY part_number ASC", ser_name, start, end)
    await conn.close()
    
    kb = InlineKeyboardBuilder()
    for p in parts:
        kb.button(text=f"{p['part_number']}-qism", callback_data=f"view_{p['id']}")
    kb.adjust(4)
    await m.answer(f"👇 {start}-{end} qismlar:", reply_markup=kb.as_markup())

# ================= ADMIN: DEL / EDIT / BROADCAST =================
@dp.message(F.text.startswith("/del") & (F.from_user.id == ADMIN_ID))
async def delete_item(m: types.Message):
    try:
        idx = int(m.text.split()[1])
        conn = await db_connect()
        await conn.execute("DELETE FROM content WHERE id=$1", idx)
        await conn.close()
        await m.answer(f"✅ ID {idx} o'chirildi.")
    except: await m.answer("Xato! Format: /del ID")

@dp.message(F.text.startswith("/edit") & (F.from_user.id == ADMIN_ID))
async def edit_item(m: types.Message, state: FSMContext):
    try:
        idx = int(m.text.split()[1])
        await state.update_data(edit_id=idx)
        await m.answer(f"ID {idx} uchun yangi ma'lumotlarni yuboring:\n\n`🎬Nomi : \n🗣Tili: \n📆 Yili: \n🎭Janr : \n🌎Davlati: `", parse_mode="Markdown")
        await state.set_state(AdminStates.waiting_for_edit_data)
    except: await m.answer("Format: /edit ID")

@dp.message(AdminStates.waiting_for_edit_data)
async def save_edit(m: types.Message, state: FSMContext):
    d = await state.get_data()
    lines = {l.split(':')[0].strip(): l.split(':')[1].strip() for l in m.text.split('\n') if ':' in l}
    conn = await db_connect()
    await conn.execute("UPDATE content SET name=$1, lang=$2, year=$3, genre=$4, country=$5 WHERE id=$6",
                       lines.get("🎬Nomi"), lines.get("🗣Tili"), lines.get("📆 Yili"), lines.get("🎭Janr"), lines.get("🌎Davlati"), d['edit_id'])
    await conn.close()
    await m.answer("✅ Yangilandi!")
    await state.clear()

@dp.message(F.text == "/send" & (F.from_user.id == ADMIN_ID))
async def send_all(m: types.Message, state: FSMContext):
    await m.answer("Barcha userlarga yuboriladigan xabarni kiriting:")
    await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast)
async def do_broadcast(m: types.Message, state: FSMContext):
    conn = await db_connect()
    users = await conn.fetch("SELECT user_id FROM users")
    await conn.close()
    for u in users:
        try: await m.copy_to(u['user_id'])
        except: continue
    await m.answer("✅ Reklama tarqatildi.")
    await state.clear()

# ... (Kino/Serial qo'shish handlerlari kodingizdagi kabi qoladi, faqat admin_id tekshiruvi bilan)
# [Ko'rish (view_content) va back_main handlerlari ham tartiblandi]

@dp.callback_query(F.data.startswith("view_"))
async def view_content(c: types.CallbackQuery):
    cid = int(c.data.split("_")[1])
    conn = await db_connect()
    res = await conn.fetchrow("SELECT * FROM content WHERE id=$1", cid)
    await conn.close()
    
    footer = "\n————————————————\n📢 @cinemahubb_HD\n🤖 @cinemahub_hdbot\n————————————————"
    if res['type'] == 'kino':
        caption = f"🎬 {res['name']}\n📆 {res['year']} | 🎭 {res['genre']}\n{footer}"
    else:
        caption = f"📺 {res['parent_name']} | 🔢 {res['part_number']}-qism\n{footer}"
    await c.message.answer_video(res['file_id'], caption=caption)

@dp.message(F.text == "⬅️ Ortga")
async def back_main(m: types.Message):
    await m.answer("Asosiy menu:", reply_markup=main_menu())

async def main():
    await create_table()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())