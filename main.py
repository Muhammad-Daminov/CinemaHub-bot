import asyncio, os, re, sys
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

# 1. SOZLAMALAR
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 6427415448 
CHANNEL_ID = "@cinemahubb_HD"

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# 2. HOLATLAR (FSM)
class UserStates(StatesGroup):
    waiting_for_search = State()
    current_serial = State()

class AdminStates(StatesGroup):
    waiting_for_kino_data = State()
    waiting_for_kino_video = State()
    waiting_for_serial_name = State()
    waiting_for_serial_video = State()
    waiting_for_edit = State()
    waiting_for_mail = State()

# 3. MA'LUMOTLAR BAZASI
async def db_connect():
    return await asyncpg.connect(DATABASE_URL)

async def create_tables():
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
        part_number INT,
        parent_name TEXT,
        kino_code INT UNIQUE
    );
    CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY);
    """)
    await conn.close()

# 4. MENYULAR
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Kinolar"), KeyboardButton(text="📺 Seriallar")],
            [KeyboardButton(text="🔎 Qidirish"), KeyboardButton(text="📊 Statistika")]
        ],
        resize_keyboard=True,
        is_persistent=True # Tarjima Play botidek tugma ichida turadi
    )

def get_reply_list(items, prefix):
    kb = ReplyKeyboardBuilder()
    for item in items:
        name = item['name'] if 'name' in item else item['parent_name']
        kb.button(text=f"{prefix} {name}")
    kb.button(text="⬅️ Bosh sahifa")
    kb.adjust(2) # 1 qatorda 2 ta tugma
    return kb.as_markup(resize_keyboard=True)

# 5. HANDLERLAR (FOYDALANUVCHI)
@dp.message(F.text == "/start")
async def start_cmd(m: types.Message):
    conn = await db_connect()
    await conn.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await conn.close()
    await m.answer("🎬 Xush kelibsiz! Kerakli bo'limni tanlang:", reply_markup=main_menu())

@dp.message(F.text == "🎬 Kinolar")
async def show_all_movies(m: types.Message):
    conn = await db_connect()
    movies = await conn.fetch("SELECT name FROM content WHERE type='kino' ORDER BY id DESC")
    await conn.close()
    if not movies: return await m.answer("Hozircha kinolar yo'q.")
    await m.answer("🍿 Kinolardan birini tanlang:", reply_markup=get_reply_list(movies, "🎬"))

@dp.message(F.text.startswith("🎬 "))
async def send_movie(m: types.Message):
    name = m.text.replace("🎬 ", "")
    conn = await db_connect()
    res = await conn.fetchrow("SELECT * FROM content WHERE name=$1 AND type='kino'", name)
    await conn.close()
    if res:
        cap = f"🎬 {res['name']}\n📆 {res['year']} | 🎭 {res['genre']}\n🆔 Kod: {res['kino_code']}"
        await m.answer_video(res['file_id'], caption=cap)

@dp.message(F.text == "📺 Seriallar")
async def show_serials(m: types.Message):
    conn = await db_connect()
    serials = await conn.fetch("SELECT DISTINCT parent_name FROM content WHERE type='part' ORDER BY parent_name")
    await conn.close()
    if not serials: return await m.answer("Hozircha seriallar yo'q.")
    await m.answer("📺 Serialni tanlang:", reply_markup=get_reply_list(serials, "📺"))

@dp.message(F.text.startswith("📺 "))
async def select_serial(m: types.Message, state: FSMContext):
    name = m.text.replace("📺 ", "")
    conn = await db_connect()
    count = await conn.fetchval("SELECT COUNT(*) FROM content WHERE parent_name=$1", name)
    await conn.close()
    
    await state.update_data(current_ser=name)
    kb = ReplyKeyboardBuilder()
    for i in range(1, count + 1, 10):
        end = min(i + 9, count)
        kb.button(text=f"📦 {i}-{end} qismlar")
    kb.button(text="⬅️ Bosh sahifa")
    kb.adjust(2)
    await m.answer(f"🎬 {name} qismlarini tanlang:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.regexp(r'📦 \d+-\d+ qismlar'))
async def send_parts_bulk(m: types.Message, state: FSMContext):
    data = await state.get_data()
    ser_name = data.get('current_ser')
    nums = re.findall(r'\d+', m.text)
    start, end = int(nums[0]), int(nums[1])
    
    conn = await db_connect()
    parts = await conn.fetch("SELECT file_id, part_number FROM content WHERE parent_name=$1 AND part_number BETWEEN $2 AND $3 ORDER BY part_number ASC", ser_name, start, end)
    await conn.close()
    
    await m.answer(f"🚀 {ser_name} {start}-{end} qismlar yuborilmoqda...")
    for p in parts:
        await m.answer_video(p['file_id'], caption=f"📺 {ser_name} | {p['part_number']}-qism")
        await asyncio.sleep(0.5)

@dp.message(F.text == "⬅️ Bosh sahifa")
async def back_home(m: types.Message):
    await m.answer("Asosiy menyu:", reply_markup=main_menu())

# 6. ADMIN QISMI (KODLAR TARTIBI VA REKLAMA)
@dp.message(F.text == "/send", F.from_user.id == ADMIN_ID)
async def start_mail(m: types.Message, state: FSMContext):
    await m.answer("Userlarga yuboriladigan xabarni kiriting:")
    await state.set_state(AdminStates.waiting_for_mail)

@dp.message(AdminStates.waiting_for_mail)
async def broadcast(m: types.Message, state: FSMContext):
    conn = await db_connect()
    users = await conn.fetch("SELECT user_id FROM users")
    await conn.close()
    for u in users:
        try: await m.copy_to(u['user_id'])
        except: continue
    await m.answer("✅ Xabar yuborildi.")
    await state.clear()

@dp.message(F.text == "/add", F.from_user.id == ADMIN_ID)
async def add_start(m: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎬 Kino", callback_data="add_k")
    kb.button(text="📺 Serial", callback_data="add_s")
    await m.answer("Nima qo'shasiz?", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "add_k")
async def add_k_form(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("Kino ma'lumotlarini yuboring (Shablon: Nomi: Yili: Janri:)")
    await state.set_state(AdminStates.waiting_for_kino_data)

@dp.message(AdminStates.waiting_for_kino_data)
async def save_k_data(m: types.Message, state: FSMContext):
    await state.update_data(k_info=m.text)
    await m.answer("Endi kino videosini yuboring:")
    await state.set_state(AdminStates.waiting_for_kino_video)

@dp.message(AdminStates.waiting_for_kino_video, F.video)
async def save_k_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    info = data['k_info'].split(':')
    conn = await db_connect()
    # Faqat kinolar uchun kodlash
    last_code = await conn.fetchval("SELECT MAX(kino_code) FROM content WHERE type='kino'")
    new_code = (last_code or 0) + 1
    await conn.execute("""
        INSERT INTO content(type, name, year, genre, file_id, kino_code)
        VALUES('kino', $1, $2, $3, $4, $5)
    """, info[0], info[1], info[2], m.video.file_id, new_code)
    await conn.close()
    await m.answer(f"✅ Kino saqlandi. Kod: {new_code}")
    await state.clear()

# 7. RUN
async def main():
    await create_tables()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())