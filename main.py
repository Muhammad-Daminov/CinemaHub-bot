import asyncio, os, re
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BotCommand
from dotenv import load_dotenv

# .env fayldan ma'lumotlarni yuklash
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 6427415448

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- State (Holat)lar ---
class UserStates(StatesGroup):
    main = State()
    search_menu = State()
    waiting_query = State()
    in_serial_list = State()
    viewing_parts = State()
    waiting_mailing = State()

async def db_connect():
    return await asyncpg.connect(DATABASE_URL)

# ================= MENULAR (REPLY) =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Kinolar"), KeyboardButton(text="📺 Seriallar")],
            [KeyboardButton(text="🔎 Qidirish")]
        ],
        resize_keyboard=True, is_persistent=True
    )

def search_options():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📅 Yili bo'yicha")
    kb.button(text="🎭 Janri bo'yicha")
    kb.button(text="🔢 Kodi bo'yicha")
    kb.button(text="📝 Nomi bo'yicha")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

# ================= HANDLERLAR =================

@dp.message(F.text == "/start")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(UserStates.main)
    # Userni bazaga saqlash (Mailing uchun)
    conn = await db_connect()
    await conn.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await conn.close()
    await m.answer("🎬 KinoMarkaz HD botiga xush kelibsiz!", reply_markup=main_menu())

# --- KINOLAR BO'LIMI (1 QATORDAN 2 TADA) ---
@dp.message(F.text == "🎬 Kinolar")
async def show_all_movies(m: types.Message):
    conn = await db_connect()
    movies = await conn.fetch("SELECT name, year FROM content WHERE type='kino' ORDER BY id DESC")
    await conn.close()
    
    if not movies:
        return await m.answer("Hozircha kinolar yo'q.")
    
    kb = ReplyKeyboardBuilder()
    for row in movies:
        kb.button(text=f"🎥 {row['name']} ({row['year']})")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("Kinolardan birini tanlang:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.startswith("🎥 "))
async def send_movie_by_name(m: types.Message):
    # Nomi orqali srazi yuborish
    name_full = m.text.replace("🎥 ", "")
    name = re.sub(r' \(\d+\)', '', name_full).strip()
    conn = await db_connect()
    res = await conn.fetchrow("SELECT * FROM content WHERE name ILIKE $1 AND type='kino'", name)
    await conn.close()
    
    if res:
        caption = f"🎬 {res['name']}\n📆 Yili: {res['year']}\n🎭 Janri: {res['genre']}\n🆔 Kod: {res['id']}"
        await m.answer_video(res['file_id'], caption=caption)

# --- QIDIRISH TIZIMI ---
@dp.message(F.text == "🔎 Qidirish")
async def open_search(m: types.Message, state: FSMContext):
    await state.set_state(UserStates.search_menu)
    await m.answer("Qidiruv turini tanlang:", reply_markup=search_options())

@dp.message(F.text.in_(["📅 Yili bo'yicha", "🎭 Janri bo'yicha", "🔢 Kodi bo'yicha", "📝 Nomi bo'yicha"]), UserStates.search_menu)
async def ask_query(m: types.Message, state: FSMContext):
    await state.update_data(search_type=m.text)
    await state.set_state(UserStates.waiting_query)
    await m.answer(f"{m.text} uchun ma'lumot kiriting:", 
                   reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Orqaga")]], resize_keyboard=True))

@dp.message(UserStates.waiting_query)
async def execute_search(m: types.Message, state: FSMContext):
    if m.text == "⬅️ Orqaga":
        await open_search(m, state)
        return

    data = await state.get_data()
    stype = data.get("search_type")
    query = m.text.strip()
    conn = await db_connect()
    
    res = []
    if "Kodi" in stype:
        if query.isdigit():
            row = await conn.fetchrow("SELECT * FROM content WHERE id=$1 AND type='kino'", int(query))
            if row: res = [row]
        else:
            await m.answer("🔢 Faqat raqam kiriting!")
            await conn.close()
            return
    elif "Yili" in stype:
        res = await conn.fetch("SELECT * FROM content WHERE year=$1 AND type='kino'", query)
    elif "Janri" in stype:
        res = await conn.fetch("SELECT * FROM content WHERE genre ILIKE $1 AND type='kino'", f"%{query}%")
    else:
        res = await conn.fetch("SELECT * FROM content WHERE name ILIKE $1 AND type='kino'", f"%{query}%")
    
    await conn.close()
    if not res:
        await m.answer("❌ Topilmadi.")
    else:
        for r in res:
            await m.answer_video(r['file_id'], caption=f"🎬 {r['name']}\n🆔 Kod: {r['id']}")

# --- SERIAL BO'LIMI ---
@dp.message(F.text == "📺 Seriallar")
async def show_serials(m: types.Message, state: FSMContext):
    await state.set_state(UserStates.in_serial_list)
    conn = await db_connect()
    names = await conn.fetch("SELECT parent_name FROM content WHERE type='part' GROUP BY parent_name ORDER BY MIN(id) ASC")
    await conn.close()
    
    kb = ReplyKeyboardBuilder()
    for row in names:
        kb.button(text=f"📺 {row['parent_name']}")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("📺 Serialni tanlang:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.startswith("📺 "), UserStates.in_serial_list)
async def serial_parts_groups(m: types.Message, state: FSMContext):
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
async def send_all_parts(m: types.Message, state: FSMContext):
    data = await state.get_data()
    ser_name = data.get('current_ser')
    nums = re.findall(r'\d+', m.text)
    start, end = int(nums[0]), int(nums[1])
    
    conn = await db_connect()
    parts = await conn.fetch("SELECT file_id, part_number FROM content WHERE parent_name=$1 AND part_number BETWEEN $2 AND $3 ORDER BY part_number ASC", ser_name, start, end)
    await conn.close()
    
    for p in parts:
        await m.answer_video(p['file_id'], caption=f"📺 {ser_name} | {p['part_number']}-qism")
        await asyncio.sleep(0.4)

# --- SMART BACK & MAILING ---
@dp.message(F.text.in_(["⬅️ Orqaga", "⬅️ Bosh menyuga"]))
async def universal_back(m: types.Message, state: FSMContext):
    curr = await state.get_state()
    if curr == UserStates.viewing_parts.state:
        await show_serials(m, state)
    elif curr == UserStates.waiting_query.state:
        await open_search(m, state)
    else:
        await start_cmd(m, state)

@dp.message(F.text == "/send", F.from_user.id == ADMIN_ID)
async def start_mail(m: types.Message, state: FSMContext):
    await m.answer("Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:")
    await state.set_state(UserStates.waiting_mailing)

@dp.message(UserStates.waiting_mailing, F.from_user.id == ADMIN_ID)
async def broadcast(m: types.Message, state: FSMContext):
    conn = await db_connect()
    users = await conn.fetch("SELECT user_id FROM users")
    await conn.close()
    for u in users:
        try: await m.copy_to(u['user_id'])
        except: continue
    await m.answer("✅ Yuborildi!")
    await state.set_state(UserStates.main)

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())