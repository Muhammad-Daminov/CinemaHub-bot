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
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ADMIN_ID2 = int(os.getenv("ADMIN_ID2"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- MAJBURIY OBUNA SOZLAMALARI ---
CHANNELS = ["@cinemahubb_HD"] 

async def check_sub(user_id: int, bot: Bot) -> bool:
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            continue
    return True

def get_sub_keyboard():
    kb = InlineKeyboardBuilder()
    for channel in CHANNELS:
        kb.button(text="📢 Kanalga a'zo bo'lish", url=f"https://t.me/{channel.replace('@', '')}")
    kb.button(text="✅ Tekshirish", callback_data="check_subscription")
    kb.adjust(1)
    return kb.as_markup()

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
    in_drama_list = State()       
    viewing_drama_parts = State() 
    waiting_mailing = State()

class AdminStates(StatesGroup):
    choosing_type = State()
    waiting_kino_template = State()
    waiting_kino_video = State()
    
    # Serial uchun
    waiting_serial_name = State()
    waiting_serial_lang = State() 
    waiting_serial_videos = State()
    
    # Drama uchun
    waiting_drama_name = State()
    waiting_drama_lang = State()  
    waiting_drama_videos = State()
    
    choosing_del_method = State()
    waiting_del_query = State()

async def db_connect():
    return await asyncpg.connect(DATABASE_URL)

# ================= MENULAR =================

def search_options():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📅 Yili bo'yicha")
    kb.button(text="🎭 Janri bo'yicha")
    kb.button(text="🔢 Kodi bo'yicha")
    kb.button(text="📝 Nomi bo'yicha")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

def main_menu(user_id: int):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Kinolar")
    kb.button(text="📺 Seriallar")
    kb.button(text="🎭 Dramalar") 
    kb.button(text="🔎 Qidirish")
    if user_id in [ADMIN_ID, ADMIN_ID2]:
        kb.button(text="➕ Qo'shish")
        kb.button(text="🗑 O'chirish")
        kb.button(text="📢 Reklama")
    kb.button(text="✍️ Zakaz berish")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

# ================= OBUNA TASDIQLASH HANDLERI =================

@dp.callback_query(F.data == "check_subscription")
async def check_callback(call: types.CallbackQuery, state: FSMContext, bot: Bot):
    if await check_sub(call.from_user.id, bot):
        await call.answer("✅ Rahmat! Obuna tasdiqlandi.", show_alert=True)
        await call.message.delete()
        await state.set_state(UserStates.main)
        await call.message.answer("🎬 Bot ochildi! Bo'limni tanlang:", reply_markup=main_menu(call.from_user.id))
    else:
        await call.answer("❌ Siz hali kanalga a'zo bo'lmagansiz!", show_alert=True)

# ================= ADMIN: O'CHIRISH =================

@dp.message(F.text == "🗑 O'chirish", F.from_user.id.in_([ADMIN_ID, ADMIN_ID2]))
@dp.message(F.text.startswith("/del"), F.from_user.id.in_([ADMIN_ID, ADMIN_ID2]))
async def delete_start(m: types.Message, state: FSMContext):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🔢 Kod orqali o'chirish")
    kb.button(text="📝 Nomi orqali o'chirish")
    kb.button(text="⬅️ Orqaga")
    kb.adjust(2)
    await m.answer("O'chirish usulini tanlang:", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(AdminStates.choosing_del_method)

@dp.message(AdminStates.choosing_del_method, F.text.contains("orqali o'chirish"))
async def ask_del_query(m: types.Message, state: FSMContext):
    method = "kodini" if "Kod" in m.text else "nomini"
    await state.update_data(del_method=m.text)
    await m.answer(f"O'chirmoqchi bo'lgan kontent {method} kiriting:")
    await state.set_state(AdminStates.waiting_del_query)

@dp.message(AdminStates.waiting_del_query)
async def process_delete(m: types.Message, state: FSMContext):
    data = await state.get_data()
    method = data.get("del_method")
    query = m.text.strip()
    conn = await db_connect()
    
    if "Kod" in method:
        if query.isdigit():
            res = await conn.execute("DELETE FROM content WHERE id=$1", int(query))
            msg = "✅ Kod bo'yicha o'chirildi!" if "1" in res else "❌ Topilmadi."
        else: msg = "⚠️ Kod faqat raqam bo'ladi!"
    else:
        res = await conn.execute("DELETE FROM content WHERE name ILIKE $1 OR parent_name ILIKE $1", f"%{query}%")
        msg = f"✅ '{query}' bo'yicha ma'lumotlar o'chirildi!"

    await conn.close()
    await m.answer(msg, reply_markup=main_menu(m.from_user.id))
    await state.set_state(UserStates.main)

# ================= ADMIN: QO'SHISH =================

@dp.message(F.text == "➕ Qo'shish", F.from_user.id.in_([ADMIN_ID, ADMIN_ID2]))
async def admin_add_menu(m: types.Message, state: FSMContext):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Yangi Kino")
    kb.button(text="📺 Serial")
    kb.button(text="🎭 Drama") 
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("Nima qo'shmoqchisiz?", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(AdminStates.choosing_type)

# --- KINO QO'SHISH (MUTLOQ XATOSIZ VARIANT) ---

@dp.message(AdminStates.choosing_type, F.text == "🎬 Yangi Kino")
async def add_kino_step1(m: types.Message, state: FSMContext):
    shablon = "🎬 Nomi:\n\n📆 Yili:\n🗣️ Tili:\n🎭 Janr:\n🌎 Davlati:"
    await m.answer(f"Quyidagi shablonni to'ldirib yuboring:\n\n`{shablon}`", parse_mode="Markdown")
    # Admin holatini shablon kutish rejimiga o'tkazamiz
    await state.set_state(AdminStates.waiting_kino_template)

@dp.message(AdminStates.waiting_kino_template)
async def add_kino_step2(m: types.Message, state: FSMContext):
    # Admin yuborgan matnni saqlab qo'yamiz
    await state.update_data(kino_info=m.text)
    await m.answer("✅ Ma'lumotlar qabul qilindi. Endi videoni yuboring:")
    # Admin holatini video kutish rejimiga o'tkazamiz
    await state.set_state(AdminStates.waiting_kino_video)

@dp.message(AdminStates.waiting_kino_video, F.video)
async def save_kino_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    info = data.get("kino_info", "")
    
    try:
        # RegEx orqali matn ichidan kalit so'zlarni aqlli qidirish
        name_match = re.search(r"(?:Nomi|🎬)\s*:\s*(.+)", info, re.IGNORECASE)
        year_match = re.search(r"(?:Yili|📆)\s*:\s*(\d+)", info, re.IGNORECASE)
        lang_match = re.search(r"(?:Tili|🗣️|Til)\s*:\s*(.+)", info, re.IGNORECASE)
        genre_match = re.search(r"(?:Janr|🎭)\s*:\s*(.+)", info, re.IGNORECASE)
        country_match = re.search(r"(?:Davlati|🌎|Davlat)\s*:\s*(.+)", info, re.IGNORECASE)

        # Agar eng asosiylari (nomi va yili) topilmasa, xatoga otamiz
        if not name_match or not year_match:
            raise ValueError("Kino nomi yoki yili shablonda topilmadi!")

        name = name_match.group(1).strip()
        year = year_match.group(1).strip()
        lang = lang_match.group(1).strip() if lang_match else "O'zbekcha"
        genre = genre_match.group(1).strip() if genre_match else "Noma'lum"
        country = country_match.group(1).strip() if country_match else "Noma'lum"

        # Bazaga ulanish va saqlash
        conn = await db_connect()
        last_id = await conn.fetchval("SELECT MAX(id) FROM content WHERE type='kino'")
        new_id = (last_id or 0) + 1
        
        await conn.execute(
            "INSERT INTO content(id, type, name, year, genre, lang, country, file_id) VALUES($1, 'kino', $2, $3, $4, $5, $6, $7)", 
            new_id, name, year, genre, lang, country, m.video.file_id
        )
        await conn.close()
        
        await m.answer(f"✅ Kino muvaffaqiyatli saqlandi!\n🆔 Kod: {new_id}", reply_markup=main_menu(m.from_user.id))
        await state.set_state(UserStates.main)
        
    except Exception as e:
        print(f"Kino saqlashda xato: {e}")
        await m.answer("❌ Xato! Shablonni to'g'ri to'ldiring va qaytadan videoni yuboring.\n\n")

# --- SERIAL QO'SHISH ---
@dp.message(AdminStates.choosing_type, F.text == "📺 Serial")
async def add_serial_step1(m: types.Message, state: FSMContext):
    await m.answer("Serial nomini kiriting:")
    await state.set_state(AdminStates.waiting_serial_name)

@dp.message(AdminStates.waiting_serial_name)
async def add_serial_step2(m: types.Message, state: FSMContext):
    await state.update_data(ser_name=m.text.strip())
    await m.answer("Serial tilini kiriting:")
    await state.set_state(AdminStates.waiting_serial_lang)

@dp.message(AdminStates.waiting_serial_lang)
async def add_serial_step3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    ser_name = data.get("ser_name")
    await state.update_data(ser_lang=m.text.strip())
    await m.answer(f"🎬 '{ser_name}' uchun videolarni bittadan yuboring. \nTugatgach '✅ Tamom' tugmasini bosing.",
                   reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Tamom")]], resize_keyboard=True))
    await state.set_state(AdminStates.waiting_serial_videos)

@dp.message(AdminStates.waiting_serial_videos, F.video)
async def save_serial_recursive(m: types.Message, state: FSMContext):
    data = await state.get_data()
    ser_name = data.get("ser_name")
    ser_lang = data.get("ser_lang")
    conn = await db_connect()
    last_part = await conn.fetchval("SELECT MAX(part_number) FROM content WHERE parent_name=$1 AND type='part'", ser_name)
    new_part = (last_part or 0) + 1
    await conn.execute("INSERT INTO content(type, parent_name, part_number, lang, file_id) VALUES('part', $1, $2, $3, $4)", 
                       ser_name, new_part, ser_lang, m.video.file_id)
    await conn.close()
    await m.answer(f"✅ {new_part}-qism qabul qilindi! yuboring...")

@dp.message(AdminStates.waiting_serial_videos, F.text == "✅ Tamom")
async def finish_serial_add(m: types.Message, state: FSMContext):
    await m.answer("✅ Serial qismlari muvaffaqiyatli saqlandi!", reply_markup=main_menu(m.from_user.id))
    await state.set_state(UserStates.main)

# --- DRAMA QO'SHISH ---
@dp.message(AdminStates.choosing_type, F.text == "🎭 Drama")
async def add_drama_step1(m: types.Message, state: FSMContext):
    await m.answer("Drama nomini kiriting:")
    await state.set_state(AdminStates.waiting_drama_name)

@dp.message(AdminStates.waiting_drama_name)
async def add_drama_step2(m: types.Message, state: FSMContext):
    await state.update_data(drama_name=m.text.strip())
    await m.answer("Drama tilini kiriting:")
    await state.set_state(AdminStates.waiting_drama_lang)

@dp.message(AdminStates.waiting_drama_lang)
async def add_drama_step3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    drama_name = data.get("drama_name")
    await state.update_data(drama_lang=m.text.strip())
    await m.answer(f"🎬 '{drama_name}' uchun videolarni bittadan yuboring.. \nTugatgach '✅ Tamom' tugmasini bosing.",
                   reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Tamom")]], resize_keyboard=True))
    await state.set_state(AdminStates.waiting_drama_videos)

@dp.message(AdminStates.waiting_drama_videos, F.video)
async def save_drama_recursive(m: types.Message, state: FSMContext):
    data = await state.get_data()
    drama_name = data.get("drama_name")
    drama_lang = data.get("drama_lang")
    conn = await db_connect()
    last_part = await conn.fetchval("SELECT MAX(part_number) FROM content WHERE parent_name=$1 AND type='drama'", drama_name)
    new_part = (last_part or 0) + 1
    await conn.execute("INSERT INTO content(type, parent_name, part_number, lang, file_id) VALUES('drama', $1, $2, $3, $4)", 
                       drama_name, new_part, drama_lang, m.video.file_id)
    await conn.close()
    await m.answer(f"✅ {new_part}-qism qabul qilindi! yuboring...")

@dp.message(AdminStates.waiting_drama_videos, F.text == "✅ Tamom")
async def finish_drama_add(m: types.Message, state: FSMContext):
    await m.answer("✅ Drama qismlari muvaffaqiyatli saqlandi!", reply_markup=main_menu(m.from_user.id))
    await state.set_state(UserStates.main)


# ================= FOYDALANUVCHI QISMI =================

@dp.message(F.text == "/start")
async def start_cmd(m: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    
    if not await check_sub(m.from_user.id, bot):
        await m.answer("⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'lishingiz kerak!", reply_markup=get_sub_keyboard())
        return

    await state.set_state(UserStates.main)
    conn = await db_connect()
    await conn.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await conn.close()

    user_name = m.from_user.full_name

    await m.answer(f"👋Assalamu Alaykum hurmatli, {user_name}!\n\n🎬 KinoMarkaz HD botiga xush kelibsiz! ✨", reply_markup=main_menu(m.from_user.id))

@dp.message(F.text == "🎬 Kinolar")
async def show_all_movies(m: types.Message, bot: Bot):
    if not await check_sub(m.from_user.id, bot):
        return await m.answer("⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'ling!", reply_markup=get_sub_keyboard())

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
async def send_movie_by_name(m: types.Message, bot: Bot):
    if not await check_sub(m.from_user.id, bot):
        return await m.answer("⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'ling!", reply_markup=get_sub_keyboard())

    name_full = m.text.replace("🎥 ", "")
    name = re.sub(r' \(\d+\)', '', name_full).strip()
    conn = await db_connect()
    res = await conn.fetchrow("SELECT * FROM content WHERE name ILIKE $1 AND type='kino'", name)
    await conn.close()
    if res:
        caption = (f"🎬 Nomi: {res['name']}\n📆 Yili: {res['year']}\n🗣️ Tili: {res['lang']}\n"
                   f"🎭 Janri: {res['genre']}\n🌎 Davlati: {res['country']}\n🆔 Kod: {res['id']}{FOOTER_TEXT}")
        await m.answer_video(res['file_id'], caption=caption)

@dp.message(F.text == "🔎 Qidirish")
async def open_search(m: types.Message, state: FSMContext, bot: Bot):
    if not await check_sub(m.from_user.id, bot):
        return await m.answer("⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'ling!", reply_markup=get_sub_keyboard())
    await state.set_state(UserStates.search_menu)
    await m.answer("Qidiruv turini tanlang:", reply_markup=search_options())

@dp.message(F.text.in_(["📅 Yili bo'yicha", "🎭 Janri bo'yicha", "🔢 Kodi bo'yicha", "📝 Nomi bo'yicha"]), UserStates.search_menu)
async def set_search_type(m: types.Message, state: FSMContext):
    await state.update_data(search_type=m.text)
    await state.set_state(UserStates.waiting_query)
    await m.answer(f"{m.text} uchun ma'lumot kiriting:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Orqaga")]], resize_keyboard=True))

@dp.message(UserStates.waiting_query)
async def execute_search(m: types.Message, state: FSMContext, bot: Bot):
    if m.text == "⬅️ Orqaga":
        await open_search(m, state, bot)
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
    
    if not res: 
        await m.answer("❌ Topilmadi.")
    else:
        for r in res:
            caption = (f"🎬 Nomi: {r['name']}\n📆 Yili: {r['year']}\n🗣️ Tili: {r['lang']}\n"
                       f"🎭 Janri: {r['genre']}\n🌎 Davlati: {r['country']}\n🆔 Kod: {r['id']}{FOOTER_TEXT}")
            await m.answer_video(r['file_id'], caption=caption)

# --- SERIAL QISMI ---
@dp.message(F.text == "📺 Seriallar")
async def show_serials(m: types.Message, state: FSMContext, bot: Bot):
    if not await check_sub(m.from_user.id, bot):
        return await m.answer("⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'ling!", reply_markup=get_sub_keyboard())
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
    count = await conn.fetchval("SELECT COUNT(*) FROM content WHERE parent_name=$1 AND type='part'", ser_name)
    await conn.close()
    kb = ReplyKeyboardBuilder()
    for i in range(1, count + 1, 10):
        end = min(i + 9, count)
        kb.button(text=f"🔢 {i}-{end} qismlar")
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
    parts = await conn.fetch("SELECT file_id, part_number, lang FROM content WHERE parent_name=$1 AND type='part' AND part_number BETWEEN $2 AND $3 ORDER BY part_number ASC", ser_name, start, end)
    await conn.close()
    for p in parts:
        await m.answer_video(p['file_id'], caption=f"📺 {ser_name} | {p['part_number']}-qism\n🗣️ Tili: {p['lang']}{FOOTER_TEXT}")
        await asyncio.sleep(0.4)

# --- DRAMA QISMI ---
@dp.message(F.text == "🎭 Dramalar")
async def show_dramas(m: types.Message, state: FSMContext, bot: Bot):
    if not await check_sub(m.from_user.id, bot):
        return await m.answer("⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'ling!", reply_markup=get_sub_keyboard())
    await state.set_state(UserStates.in_drama_list)
    conn = await db_connect()
    names = await conn.fetch("SELECT parent_name FROM content WHERE type='drama' GROUP BY parent_name ORDER BY MIN(id) ASC")
    await conn.close()
    kb = ReplyKeyboardBuilder()
    for row in names:
        kb.button(text=f"🎭 {row['parent_name']}")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("🎭 Dramani tanlang:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.startswith("🎭 "), UserStates.in_drama_list)
async def drama_parts_groups(m: types.Message, state: FSMContext):
    drama_name = m.text.replace("🎭 ", "")
    await state.update_data(current_drama=drama_name)
    await state.set_state(UserStates.viewing_drama_parts)
    conn = await db_connect()
    count = await conn.fetchval("SELECT COUNT(*) FROM content WHERE parent_name=$1 AND type='drama'", drama_name)
    await conn.close()
    kb = ReplyKeyboardBuilder()
    for i in range(1, count + 1, 10):
        end = min(i + 9, count)
        kb.button(text=f"🔢 {i}-{end} qismlar")
    kb.button(text="⬅️ Orqaga")
    kb.adjust(2)
    await m.answer(f"🎬 {drama_name} qismlari:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.contains("qismlar"), UserStates.viewing_drama_parts)
async def send_all_drama_parts(m: types.Message, state: FSMContext):
    data = await state.get_data()
    drama_name = data.get('current_drama')
    nums = re.findall(r'\d+', m.text)
    start, end = int(nums[0]), int(nums[1])
    conn = await db_connect()
    parts = await conn.fetch("SELECT file_id, part_number, lang FROM content WHERE parent_name=$1 AND type='drama' AND part_number BETWEEN $2 AND $3 ORDER BY part_number ASC", drama_name, start, end)
    await conn.close()
    for p in parts:
        await m.answer_video(p['file_id'], caption=f"🎭 {drama_name} | {p['part_number']}-qism\n🗣️ Tili: {p['lang']}{FOOTER_TEXT}")
        await asyncio.sleep(0.4)

# --- SMART BACK (XATOSIZ VA BAQUVVAT VARIANT) ---
@dp.message(F.text.in_(["⬅️ Orqaga", "⬅️ Bosh menyuga", "Orqaga", "Bosh menyuga"]))
async def universal_back(m: types.Message, state: FSMContext, bot: Bot):
    curr = await state.get_state()
    
    # Har qanday holatda ham (State) orqaga qaytganda avvalgi keraksiz ma'lumotlarni tozalaymiz
    if curr:
        await state.clear()
        
    # Foydalanuvchini asosiy holatga o'tkazamiz
    await state.set_state(UserStates.main)
    
    # Ortiqcha tekshiruvlarsiz to'g'ridan-to'g'ri bosh menyuni qaytaramiz
    await m.answer("📋 Asosiy menyu:", reply_markup=main_menu(m.from_user.id))

# --- REKLAMA ---
@dp.message(F.text == "📢 Reklama", F.from_user.id.in_([ADMIN_ID, ADMIN_ID2]))
async def start_mail(m: types.Message, state: FSMContext):
    await m.answer("Xabarni kiriting:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Orqaga")]], resize_keyboard=True))
    await state.set_state(UserStates.waiting_mailing)

@dp.message(UserStates.waiting_mailing, F.from_user.id.in_([ADMIN_ID, ADMIN_ID2]))
async def broadcast(m: types.Message, state: FSMContext, bot: Bot):
    if m.text == "⬅️ Orqaga":
        await start_cmd(m, state, bot)
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

# ---------------- ZAKAZ TIZIMI KODI ----------------
class OrderState(StatesGroup):
    choosing_type = State()
    waiting_content = State()

@dp.message(F.text == "✍️ Zakaz berish")
async def start_order(message: types.Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎬 Kino", callback_data="order_type:Kino")
    kb.button(text="📺 Serial", callback_data="order_type:Serial")
    kb.button(text="🧸 Multfilm", callback_data="order_type:Multfilm")
    kb.button(text="🎭 Drama", callback_data="order_type:Drama")
    kb.adjust(2)
    await message.answer("Qaysi turdagi kontentga buyurtma bermoqchisiz? Tanlang:", reply_markup=kb.as_markup())
    await state.set_state(OrderState.choosing_type)

@dp.callback_query(F.data.startswith("order_type:"), OrderState.choosing_type)
async def process_type(callback: types.CallbackQuery, state: FSMContext):
    selected_type = callback.data.split(":")[1]
    await state.update_data(order_type=selected_type)
    type_labels = {"Kino": "kinoingizdan", "Serial": "seriallingizdan", "Multfilm": "multfilmingizdan", "Drama": "dramangizdan"}
    label = type_labels.get(selected_type, "kontentingizdan")
    await callback.message.answer(f"Qidirayotgan {label} qisqa video yoki nomini yozib qoldiring, biz uni topishga harakat qilamiz.")
    await callback.answer()
    await state.set_state(OrderState.waiting_content)

@dp.message(OrderState.waiting_content, F.text | F.video | F.document)
async def process_content(message: types.Message, state: FSMContext, bot):
    user_data = await state.get_data()
    order_type = user_data.get("order_type")
    user_name = message.from_user.full_name
    user_username = f"@{message.from_user.username}" if message.from_user.username else "Mavjud emas"
    user_id = message.from_user.id
    
    admin_text = (
        f"🚨 **Yangi Buyurtma Keldi!**\n\n"
        f"👤 **Foydalanuvchi:** {user_name}\n"
        f"🆔 **ID:** `{user_id}`\n"
        f"🌐 **Username:** {user_username}\n"
        f"🗂 **Turi:** {order_type}\n\n"
        f"📝 **Buyurtma matni/mazmuni:**\n"
    )
    
    admin_kb = InlineKeyboardBuilder()
    if message.from_user.username:
        admin_kb.button(text="💬 Alohida Chat Ochish", url=f"https://t.me/{message.from_user.username}")
    else:
        admin_kb.button(text="👤 Profilga O'tish", url=f"tg://user?id={user_id}")

    if message.text:
        admin_text += f"\"{message.text}\""
        await bot.send_message(chat_id=ADMIN_ID2, text=admin_text, reply_markup=admin_kb.as_markup(), parse_mode="Markdown")
    elif message.video:
        caption = message.caption if message.caption else "Nom yozilmadi"
        admin_text += f"\"{caption}\""
        await bot.send_video(chat_id=ADMIN_ID2, video=message.video.file_id, caption=admin_text, reply_markup=admin_kb.as_markup(), parse_mode="Markdown")
    elif message.document:
        caption = message.caption if message.caption else "Fayl yuborildi"
        admin_text += f"\"{caption}\""
        await bot.send_document(chat_id=ADMIN_ID2, document=message.document.file_id, caption=admin_text, reply_markup=admin_kb.as_markup(), parse_mode="Markdown")

    thanks_labels = {"Kino": "kinoingizni", "Serial": "seriallingizni", "Multfilm": "multfilmingizni", "Drama": "dramangizni"}
    thanks_label = thanks_labels.get(order_type, "buyurtmangizni")
    await message.answer(f"Rahmat, zakazingiz qabul qilindi! ✨\n{thanks_label.capitalize()} topganimizdan so'ng sizga albatta xabar beramiz.")
    await state.clear()
    
# --- RENDER UCHUN SOXTA PORT OCHISH ---
async def start_fake_server():
    from aiohttp import web
    app = web.Application()
    
    async def home(request):
        return web.Response(text="Bot faol!")
        
    app.router.add_get('/', home)
    
    port = int(os.environ.get("PORT", 8000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Soxta server {port}-portda ishga tushdi.")

async def main():
    try:
        from aiogram.types import BotCommand
        my_commands = [
            BotCommand(command="start", description="Botni qayta ishga tushirish (Restart)")
        ]
        await bot.set_my_commands(commands=my_commands)
        print("✅ Bot menyusi (BotCommand) muvaffaqiyatli o'rnatildi!")
    except Exception as e:
        print(f"❌ Menyu o'rnatishda xatolik yuz berdi: {e}")

    await start_fake_server()
    print("Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())