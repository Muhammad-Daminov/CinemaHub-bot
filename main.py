import asyncio
import os
import sys
import asyncpg
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

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

# 2. FSM (Holatlar)
class UserStates(StatesGroup):
    waiting_for_search_query = State()

class AdminStates(StatesGroup):
    waiting_for_kino_table = State()
    waiting_for_kino_video = State()
    waiting_for_serial_name = State()
    waiting_for_serial_video = State()
    waiting_for_edit_data = State()

# ================= DB QISMI =================
async def db_connect():
    return await asyncpg.connect(DATABASE_URL)

async def create_table():
    conn = await db_connect()
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

# ================= OBUNA TEKSHIRISH =================
async def check_sub(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# ================= MENULAR =================
def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎬 Kinolar"), KeyboardButton(text="📺 Seriallar")]
    ], resize_keyboard=True)

def kino_search_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🔎 Nomi orqali"), KeyboardButton(text="📅 Yili orqali")],
        [KeyboardButton(text="🎭 Janr orqali"), KeyboardButton(text="🔢 Kod orqali")],
        [KeyboardButton(text="⬅️ Ortga")]
    ], resize_keyboard=True)

# ================= START & SUB =================
@dp.message(F.text == "/start")
async def start_cmd(m: types.Message):
    if not await check_sub(m.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Obuna bo'ling", url=f"https://t.me/{CHANNEL_ID[1:]}")],
            [InlineKeyboardButton(text="✅ A'zo bo'ldim", callback_data="check_sub")]
        ])
        return await m.answer(f"⚠️ Botdan foydalanish uchun {CHANNEL_ID} kanaliga obuna bo'ling!", reply_markup=kb)
    await m.answer("🎬 Xush kelibsiz! Bo'limni tanlang:", reply_markup=main_menu())

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(c: types.CallbackQuery):
    if await check_sub(c.from_user.id):
        await c.message.delete()
        await c.message.answer("✅ Obuna tasdiqlandi!", reply_markup=main_menu())
    else:
        await c.answer("❌ Hali a'zo emassiz!", show_alert=True)

# ================= KINO BO'LIMI VA QIDIRUV =================
@dp.message(F.text == "🎬 Kinolar")
async def kino_section(m: types.Message):
    await m.answer("Kino qidirish turini tanlang:", reply_markup=kino_search_menu())

@dp.message(F.text.in_(["🔎 Nomi orqali", "📅 Yili orqali", "🎭 Janr orqali", "🔢 Kod orqali"]))
async def start_search(m: types.Message, state: FSMContext):
    await m.answer(f"Marhamat, {m.text.lower()}ni kiriting:")
    await state.set_state(UserStates.waiting_for_search_query)

@dp.message(UserStates.waiting_for_search_query)
async def process_search(m: types.Message, state: FSMContext):
    query = m.text
    conn = await db_connect()
    
    if query.isdigit():
        res = await conn.fetchrow("SELECT * FROM content WHERE id=$1", int(query))
    else:
        res = await conn.fetchrow("""
            SELECT * FROM content 
            WHERE name ILIKE $1 OR genre ILIKE $1 OR year ILIKE $1
            LIMIT 1
        """, f"%{query}%")
    
    await conn.close()

    if res:
        footer = "\n————————————————\n📢 Bizning kanal : @cinemahubb_HD\n————————————————\n🤖 Bizning bot: @cinemahub_hdbot\n————————————————"
        if res['type'] == 'kino':
            caption = f"🎬 Nomi: {res['name']}\n\n🗣 Til : {res['lang']}\n📆 Yil: {res['year']}\n🎭 Janr : {res['genre']}\n🌎 Davlati : {res['country']}\n{footer}"
        else:
            caption = f"📺 Serial: {res['parent_name']}\n🔢 Qism : {res['part_number']}-qism\n{footer}"
        
        await m.answer_video(res['file_id'], caption=caption)
        await state.clear()
    else:
        await m.answer("❌ Hech narsa topilmadi. Qaytadan urinib ko'ring.")

# ================= SERIAL BO'LIMI =================
@dp.message(F.text == "📺 Seriallar")
async def serial_section(m: types.Message):
    conn = await db_connect()
    names = await conn.fetch("SELECT DISTINCT parent_name FROM content WHERE type='part'")
    await conn.close()
    
    if not names:
        return await m.answer("❌ Hozircha seriallar yo'q")
    
    kb = InlineKeyboardBuilder()
    for row in names:
        kb.button(text=row['parent_name'], callback_data=f"ser_{row['parent_name']}")
    kb.adjust(2)
    await m.answer("📺 Serialni tanlang:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("ser_"))
async def show_parts(c: types.CallbackQuery):
    ser_name = c.data.split("_")[1]
    conn = await db_connect()
    parts = await conn.fetch("SELECT id, part_number FROM content WHERE parent_name=$1 ORDER BY part_number ASC", ser_name)
    await conn.close()
    
    kb = InlineKeyboardBuilder()
    for p in parts:
        kb.button(text=f"{p['part_number']}-qism", callback_data=f"view_{p['id']}")
    kb.adjust(4)
    await c.message.answer(f"🎬 {ser_name} seriali qismlari:", reply_markup=kb.as_markup())

# ================= ADMIN QISMI =================
@dp.message(F.text == "/add")
async def admin_add(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Kino qo'shish", callback_data="add_kino")],
        [InlineKeyboardButton(text="📺 Serial qo'shish", callback_data="add_serial")]
    ])
    await m.answer("Nima qo'shishni xohlaysiz?", reply_markup=kb)

@dp.callback_query(F.data == "add_kino")
async def add_kino_start(c: types.CallbackQuery, state: FSMContext):
    form = "🎬Nomi : \n🗣Tili: \n📆 Yili: \n🎭Janr : \n🌎Davlati: "
    await c.message.answer(f"Nusxa olib to'ldiring:\n\n`{form}`", parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_for_kino_table)

@dp.message(AdminStates.waiting_for_kino_table)
async def get_kino_table(m: types.Message, state: FSMContext):
    lines = m.text.split('\n')
    data = {}
    for line in lines:
        if ':' in line:
            k, v = line.split(':', 1)
            data[k.strip()] = v.strip()
    await state.update_data(kino_data=data)
    await m.answer("🎬 Endi kino videosini yuboring:")
    await state.set_state(AdminStates.waiting_for_kino_video)

@dp.message(AdminStates.waiting_for_kino_video, F.video)
async def save_kino(m: types.Message, state: FSMContext):
    st_data = await state.get_data()
    d = st_data['kino_data']
    conn = await db_connect()
    row = await conn.fetchrow("""
        INSERT INTO content(type, name, lang, year, genre, country, file_id)
        VALUES('kino', $1, $2, $3, $4, $5, $6) RETURNING id
    """, d.get("🎬Nomi"), d.get("🗣Tili"), d.get("📆 Yili"), d.get("🎭Janr"), d.get("🌎Davlati"), m.video.file_id)
    await conn.close()
    await m.answer(f"✅ Kino saqlandi! Kod: {row['id']}")
    await state.clear()

@dp.callback_query(F.data == "add_serial")
async def add_serial_start(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("Serial nomini kiriting:")
    await state.set_state(AdminStates.waiting_for_serial_name)

@dp.message(AdminStates.waiting_for_serial_name)
async def get_ser_name(m: types.Message, state: FSMContext):
    await state.update_data(ser_name=m.text)
    await m.answer(f"🎬 {m.text} uchun videolarni ketma-ket yuboring.")
    await state.set_state(AdminStates.waiting_for_serial_video)

@dp.message(AdminStates.waiting_for_serial_video, F.video)
async def save_serial_parts(m: types.Message, state: FSMContext):
    data = await state.get_data()
    ser_name = data['ser_name']
    conn = await db_connect()
    last_part = await conn.fetchval("SELECT MAX(part_number) FROM content WHERE parent_name=$1", ser_name)
    current_part = (last_part or 0) + 1
    await conn.execute("""
        INSERT INTO content(type, parent_name, part_number, file_id)
        VALUES('part', $1, $2, $3)
    """, ser_name, current_part, m.video.file_id)
    await conn.close()
    await m.answer(f"✅ {current_part}-qism qabul qilindi!")

@dp.callback_query(F.data.startswith("view_"))
async def view_content(c: types.CallbackQuery):
    cid = int(c.data.split("_")[1])
    conn = await db_connect()
    res = await conn.fetchrow("SELECT * FROM content WHERE id=$1", cid)
    await conn.close()
    footer = "\n————————————————\n📢 Bizning kanal : @cinemahubb_HD\n————————————————\n🤖 Bizning bot: @cinemahub_hdbot\n————————————————"
    if res['type'] == 'kino':
        caption = f"🎬 Nomi: {res['name']}\n\n🗣 Til : {res['lang']}\n📆 Yil: {res['year']}\n🎭 Janr : {res['genre']}\n🌎 Davlati : {res['country']}\n{footer}"
    else:
        caption = f"📺 Serial: {res['parent_name']}\n🔢 Qism : {res['part_number']}-qism\n{footer}"
    await c.message.answer_video(res['file_id'], caption=caption)

@dp.message(F.text == "⬅️ Ortga")
async def back_main(m: types.Message):
    await m.answer("Asosiy menu:", reply_markup=main_menu())

# ================= RUN =================
async def main():
    await create_table()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())