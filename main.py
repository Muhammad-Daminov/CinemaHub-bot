import asyncio, os, re
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BotCommand
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 6427415448

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- REKLAMA MATNI (FOOTER) ---
FOOTER_TEXT = (
    "\n\n————————————————\n"
    "📢 Bizning kanal : @cinemahubb_HD\n"
    "————————————————\n"
    "🤖 Bizning bot: @cinemahub_hdbot\n"
    "————————————————"
)

class UserStates(StatesGroup):
    main = State()
    search_menu = State()
    waiting_query = State()
    in_serial_list = State()
    viewing_parts = State()
    waiting_mailing = State()

class AdminStates(StatesGroup):
    choosing_type = State()
    waiting_kino_data = State()
    waiting_serial_data = State()

async def db_connect():
    return await asyncpg.connect(DATABASE_URL)

# ================= MENULAR =================

def main_menu(user_id: int):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Kinolar")
    kb.button(text="📺 Seriallar")
    kb.button(text="🔎 Qidirish")
    if user_id == ADMIN_ID:
        kb.button(text="➕ Qo'shish")
        kb.button(text="📢 Reklama")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True, is_persistent=True)

def search_options():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📅 Yili bo'yicha")
    kb.button(text="🎭 Janri bo'yicha")
    kb.button(text="🔢 Kodi bo'yicha")
    kb.button(text="📝 Nomi bo'yicha")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

# ================= ADMIN: O'CHIRISH (/del) =================

@dp.message(F.text.startswith("/del"), F.from_user.id == ADMIN_ID)
async def delete_content(m: types.Message):
    try:
        content_id = int(m.text.split()[1])
        conn = await db_connect()
        # Kinoni yoki serial qismini bazadagi ID orqali o'chirish
        res = await conn.execute("DELETE FROM content WHERE id=$1", content_id)
        await conn.close()
        
        if "DELETE 1" in res:
            await m.answer(f"✅ Kod: {content_id} bo'lgan kontent o'chirildi!")
        else:
            await m.answer("❌ Bunday kodli kontent topilmadi.")
    except:
        await m.answer("⚠️ Format: `/del 123` (123 o'rniga o'chirmoqchi bo'lgan kontent kodingiz)")

# ================= ADMIN: QO'SHISH =================

@dp.message(F.text == "➕ Qo'shish", F.from_user.id == ADMIN_ID)
async def admin_add_menu(m: types.Message, state: FSMContext):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Yangi Kino")
    kb.button(text="📺 Serial Qismi")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("Nima qo'shmoqchisiz?", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(AdminStates.choosing_type)

@dp.message(AdminStates.waiting_kino_data, F.video)
async def save_kino(m: types.Message, state: FSMContext):
    try:
        data = m.caption.split("|")
        name, year, genre, lang = data[0].strip(), data[1].strip(), data[2].strip(), data[3].strip()
        conn = await db_connect()
        last_id = await conn.fetchval("SELECT MAX(id) FROM content WHERE type='kino'")
        new_id = (last_id or 0) + 1
        await conn.execute("INSERT INTO content(id, type, name, year, genre, lang, file_id) VALUES($1, 'kino', $2, $3, $4, $5, $6)", 
                           new_id, name, year, genre, lang, m.video.file_id)
        await conn.close()
        await m.answer(f"✅ Kino saqlandi! Kodi: {new_id}")
    except: await m.answer("❌ Xato! Format: Nomi|Yili|Janri|Tili")

@dp.message(AdminStates.waiting_serial_data, F.video)
async def save_serial(m: types.Message, state: FSMContext):
    try:
        data = m.caption.split("|")
        ser_name, part_num = data[0].strip(), int(data[1].strip())
        conn = await db_connect()
        # Serial qismlari uchun ham ID berish o'chirish oson bo'lishi uchun
        await conn.execute("INSERT INTO content(type, parent_name, part_number, file_id) VALUES('part', $1, $2, $3)", 
                           ser_name, part_num, m.video.file_id)
        await conn.close()
        await m.answer(f"✅ {ser_name} ning {part_num}-qismi saqlandi!")
    except: await m.answer("❌ Xato! Format: Serial Nomi|Qism Raqami")

# ================= HANDLERLAR =================

@dp.message(F.text == "/start")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(UserStates.main)
    conn = await db_connect()
    await conn.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await conn.close()
    await m.answer("🎬 KinoMarkaz HD botiga xush kelibsiz!", reply_markup=main_menu(m.from_user.id))

@dp.message(F.text == "🎬 Kinolar")
async def show_all_movies(m: types.Message):
    conn = await db_connect()
    movies = await conn.fetch("SELECT name, year FROM content WHERE type='kino' ORDER BY id DESC")
    await conn.close()
    kb = ReplyKeyboardBuilder()
    for row in movies:
        kb.button(text=f"🎥 {row['name']} ({row['year']})")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("Kinolardan birini tanlang:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.startswith("🎥 "))
async def send_movie_by_name(m: types.Message):
    name_full = m.text.replace("🎥 ", "")
    name = re.sub(r' \(\d+\)', '', name_full).strip()
    conn = await db_connect()
    res = await conn.fetchrow("SELECT * FROM content WHERE name ILIKE $1 AND type='kino'", name)
    await conn.close()
    if res:
        # FOOTER QO'SHILDI
        caption = f"🎬 Nomi: {res['name']}\n📆 Yili: {res['year']}\n🎭 Janri: {res['genre']}\n🆔 Kod: {res['id']}{FOOTER_TEXT}"
        await m.answer_video(res['file_id'], caption=caption)

@dp.message(F.text == "🔎 Qidirish")
async def open_search(m: types.Message, state: FSMContext):
    await state.set_state(UserStates.search_menu)
    await m.answer("Qidiruv turini tanlang:", reply_markup=search_options())

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
    elif "Yili" in stype:
        res = await conn.fetch("SELECT * FROM content WHERE year=$1 AND type='kino'", query)
    elif "Janri" in stype:
        res = await conn.fetch("SELECT * FROM content WHERE genre ILIKE $1 AND type='kino'", f"%{query}%")
    else:
        res = await conn.fetch("SELECT * FROM content WHERE name ILIKE $1 AND type='kino'", f"%{query}%")
    await conn.close()
    if not res: await m.answer("❌ Topilmadi.")
    else:
        for r in res:
            # FOOTER QO'SHILDI
            await m.answer_video(r['file_id'], caption=f"🎬 {r['name']}\n🆔 Kod: {r['id']}{FOOTER_TEXT}")

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
        # FOOTER QO'SHILDI
        await m.answer_video(p['file_id'], caption=f"📺 {ser_name} | {p['part_number']}-qism{FOOTER_TEXT}")
        await asyncio.sleep(0.4)

# --- SMART BACK ---
@dp.message(F.text.in_(["⬅️ Orqaga", "⬅️ Bosh menyuga"]))
async def universal_back(m: types.Message, state: FSMContext):
    curr = await state.get_state()
    if curr == UserStates.viewing_parts.state:
        await show_serials(m, state)
    elif curr == UserStates.waiting_query.state:
        await open_search(m, state)
    elif str(curr).startswith("AdminStates"):
        await start_cmd(m, state)
    else:
        await start_cmd(m, state)

@dp.message(F.text == "📢 Reklama", F.from_user.id == ADMIN_ID)
async def start_mail(m: types.Message, state: FSMContext):
    await m.answer("Xabarni kiriting:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Orqaga")]], resize_keyboard=True))
    await state.set_state(UserStates.waiting_mailing)

@dp.message(UserStates.waiting_mailing, F.from_user.id == ADMIN_ID)
async def broadcast(m: types.Message, state: FSMContext):
    if m.text == "⬅️ Orqaga":
        await start_cmd(m, state)
        return
    conn = await db_connect()
    users = await conn.fetch("SELECT user_id FROM users")
    await conn.close()
    count = 0
    for u in users:
        try: 
            await m.copy_to(u['user_id'])
            count += 1
            await asyncio.sleep(0.05)
        except: continue
    await m.answer(f"✅ {count} ta foydalanuvchiga yuborildi!", reply_markup=main_menu(m.from_user.id))
    await state.set_state(UserStates.main)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())