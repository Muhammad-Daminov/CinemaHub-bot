import asyncio, os, re
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BotCommand, FSInputFile
from aiogram.filters import StateFilter
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
    in_multifilm_list = State()      
    viewing_multifilm_parts = State() 
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
    
    # Multifilm uchun
    waiting_multifilm_name = State()
    waiting_multifilm_lang = State()
    waiting_multifilm_videos = State()
    
    choosing_del_method = State()
    waiting_del_query = State()

async def db_connect():
    return await asyncpg.connect(DATABASE_URL)

# ================= MENULAR =================

def search_options():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📅 Yili bo'yicha")
    kb.button(text="🎭 Janri bo'yicha")
    kb.button(text="📝 Nomi bo'yicha")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

def main_menu(user_id: int):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Kinolar")
    kb.button(text="📺 Seriallar")
    kb.button(text="🎭 Dramalar") 
    kb.button(text="🧸 Multifilmlar") 
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
        
        welcome_text = (
            f"👋 Assalamu alaykum hurmatli {call.from_user.full_name}\n\n"
            f"Kino_markaz HD 🎬 botiga xush kelibsiz!\n\n\n"
            f"⚡ KINO KODINI YUBORING!"
        )
        await call.message.answer(welcome_text, reply_markup=main_menu(call.from_user.id))
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
    kb.button(text="🧸 Multifilm") 
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("Nima qo'shmoqchisiz?", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(AdminStates.choosing_type)

# --- KINO QO'SHISH ---
@dp.message(AdminStates.choosing_type, F.text == "🎬 Yangi Kino")
async def add_kino_step1(m: types.Message, state: FSMContext):
    shablon = "🎬 Nomi:\n\n📆 Yili:\n🗣️ Tili:\n🎭 Janr:\n🌎 Davlati:"
    await m.answer(f"Quyidagi shablonni to'ldirib yuboring:\n\n`{shablon}`", parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_kino_template)

@dp.message(AdminStates.waiting_kino_template)
async def add_kino_step2(m: types.Message, state: FSMContext):
    await state.update_data(kino_info=m.text)
    await m.answer("✅ Ma'lumotlar qabul qilindi. Endi videoni yuboring:")
    await state.set_state(AdminStates.waiting_kino_video)

@dp.message(AdminStates.waiting_kino_video, F.video)
async def save_kino_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    info = data.get("kino_info", "")
    
    try:
        name_match = re.search(r"(?:🎬\s*Nomi|🎬|Nomi)\s*[:\s]\s*(.+)", info, re.IGNORECASE)
        year_match = re.search(r"(?:📆\s*Yili|📆|Yili)\s*[:\s]\s*(\d+)", info, re.IGNORECASE)
        lang_match = re.search(r"(?:🗣️\s*Tili|🗣️|Tili|Til)\s*[:\s]\s*(.+)", info, re.IGNORECASE)
        genre_match = re.search(r"(?:🎭\s*Janri|🎭|Janr)\s*[:\s]\s*(.+)", info, re.IGNORECASE)
        country_match = re.search(r"(?:🌎\s*Davlati|🌎|Davlat)\s*[:\s]\s*(.+)", info, re.IGNORECASE)

        if not name_match or not year_match:
            raise ValueError("Kino nomi yoki yili shablonda topilmadi!")

        name = name_match.group(1).strip()
        year = int(year_match.group(1).strip())
        lang = lang_match.group(1).strip() if lang_match else "O'zbekcha"
        genre = genre_match.group(1).strip() if genre_match else "Noma'lum"
        country = country_match.group(1).strip() if country_match else "Noma'lum"

        conn = await db_connect()
        last_id = await conn.fetchval("SELECT MAX(id) FROM content")
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
        await m.answer(f"❌ Xato yuz berdi: {e}\n\nShablonni qaytadan tekshirib to'ldiring va videoni yuboring.")

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
    
    kb = ReplyKeyboardBuilder()
    kb.button(text="✅ Tamom")
    await m.answer(f"🎬 '{ser_name}' uchun videolarni bittadan yuboring.\nTugatgach '✅ Tamom' tugmasini bosing.",
                   reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(AdminStates.waiting_serial_videos)

@dp.message(AdminStates.waiting_serial_videos, F.video)
async def save_serial_recursive(m: types.Message, state: FSMContext):
    data = await state.get_data()
    ser_name = data.get("ser_name")
    ser_lang = data.get("ser_lang")
    
    conn = await db_connect()
    try:
        last_part = await conn.fetchval("SELECT MAX(part_number) FROM content WHERE parent_name=$1 AND type='part'", ser_name)
        new_part = (last_part or 0) + 1
        
        last_id = await conn.fetchval("SELECT MAX(id) FROM content")
        new_id = (last_id or 0) + 1
        
        await conn.execute(
            "INSERT INTO content(id, type, parent_name, name, part_number, lang, file_id) VALUES($1, 'part', $2, $3, $4, $5, $6)", 
            new_id, ser_name, f"{ser_name} {new_part}-qism", new_part, ser_lang, m.video.file_id
        )
        await m.answer(f"✅ {new_part}-qism muvaffaqiyatli saqlandi! (Kod: {new_id})\nKeyingisini yuboring yoki '✅ Tamom' tugmasini bosing...")
    except Exception as e:
        print(f"Serial saqlashda xato: {e}")
        await m.answer(f"❌ Xatolik yuz berdi: {e}\nQayta yuborib ko'ring.")
    finally:
        await conn.close()

@dp.message(AdminStates.waiting_serial_videos, F.text == "✅ Tamom")
async def finish_serial_add(m: types.Message, state: FSMContext):
    await m.answer("✅ Serialning barcha qismlari muvaffaqiyatli saqlandi va yakunlandi!", reply_markup=main_menu(m.from_user.id))
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
    
    kb = ReplyKeyboardBuilder()
    kb.button(text="✅ Tamom")
    await m.answer(f"🎬 '{drama_name}' uchun videolarni bittadan yuboring.\nTugatgach '✅ Tamom' tugmasini bosing.",
                   reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(AdminStates.waiting_drama_videos)

@dp.message(AdminStates.waiting_drama_videos, F.video)
async def save_drama_recursive(m: types.Message, state: FSMContext):
    data = await state.get_data()
    drama_name = data.get("drama_name")
    drama_lang = data.get("drama_lang")
    
    conn = await db_connect()
    try:
        last_part = await conn.fetchval("SELECT MAX(part_number) FROM content WHERE parent_name=$1 AND type='drama'", drama_name)
        new_part = (last_part or 0) + 1
        
        last_id = await conn.fetchval("SELECT MAX(id) FROM content")
        new_id = (last_id or 0) + 1
        
        await conn.execute(
            "INSERT INTO content(id, type, parent_name, name, part_number, lang, file_id) VALUES($1, 'drama', $2, $3, $4, $5, $6)", 
            new_id, drama_name, f"{drama_name} {new_part}-qism", new_part, drama_lang, m.video.file_id
        )
        await m.answer(f"✅ {new_part}-qism muvaffaqiyatli saqlandi! (Kod: {new_id})\nKeyingisini yuboring...")
    except Exception as e:
        print(f"Drama saqlashda xato: {e}")
        await m.answer(f"❌ Xatolik yuz berdi: {e}\nQayta yuborib ko'ring.")
    finally:
        await conn.close()

@dp.message(AdminStates.waiting_drama_videos, F.text == "✅ Tamom")
async def finish_drama_add(m: types.Message, state: FSMContext):
    await m.answer("✅ Drama qismlari muvaffaqiyatli yakunlandi!", reply_markup=main_menu(m.from_user.id))
    await state.set_state(UserStates.main)

# --- MULTIFILM QO'SHISH ---
@dp.message(AdminStates.choosing_type, F.text == "🧸 Multifilm")
async def add_multifilm_step1(m: types.Message, state: FSMContext):
    await m.answer("Multifilm nomini kiriting:")
    await state.set_state(AdminStates.waiting_multifilm_name)

@dp.message(AdminStates.waiting_multifilm_name)
async def add_multifilm_step2(m: types.Message, state: FSMContext):
    await state.update_data(multi_name=m.text.strip())
    await m.answer("Multifilm tilini kiriting:")
    await state.set_state(AdminStates.waiting_multifilm_lang)

@dp.message(AdminStates.waiting_multifilm_lang)
async def add_multifilm_step3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    multi_name = data.get("multi_name")
    await state.update_data(multi_lang=m.text.strip())
    await m.answer(f"🚀 '{multi_name}' multfilmi videosini yuboring (yoki kanaldan uzating):",
                   reply_markup=types.ReplyKeyboardRemove()) 
    await state.set_state(AdminStates.waiting_multifilm_videos)

@dp.message(AdminStates.waiting_multifilm_videos, F.video)
async def save_multifilm_single(m: types.Message, state: FSMContext):
    data = await state.get_data()
    multi_name = data.get("multi_name")
    multi_lang = data.get("multi_lang")
    
    conn = await db_connect()
    try:
        last_id = await conn.fetchval("SELECT MAX(id) FROM content")
        new_id = (last_id or 0) + 1
        
        await conn.execute(
            "INSERT INTO content(id, type, parent_name, name, part_number, lang, file_id) VALUES($1, 'multifilm', $2, $3, 1, $4, $5)", 
            new_id, multi_name, multi_name, multi_lang, m.video.file_id
        )
        await m.answer(f"✅ Multifilm muvaffaqiyatli saqlandi!\n🆔 Kod: {new_id}", reply_markup=main_menu(m.from_user.id))
        await state.set_state(UserStates.main)
    except Exception as e:
        print(f"Multifilm saqlashda xato: {e}")
        await m.answer(f"❌ Xatolik yuz berdi: {e}\nQayta urinib ko'ring.")
    finally:
        await conn.close()

# ================= FOYDALANUVCHI QISMI (START) =================
@dp.message(F.text == "/start")
async def start_cmd(m: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    
    welcome_text = (
        f"👋 Assalamu alaykum hurmatli {m.from_user.full_name}\n\n"
        f"Kino_markaz HD 🎬 botiga xush kelibsiz!\n\n\n"
        f"⚡ KINO KODINI YUBORING!"
    )
    
    await m.answer(
        text=welcome_text,
        reply_markup=main_menu(m.from_user.id)
    )

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

@dp.message(F.text.in_(["📅 Yili bo'yicha", "🎭 Janri bo'yicha", "📝 Nomi bo'yicha"]), UserStates.search_menu)
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
    
    if "Yili" in stype:
        try:
            res = await conn.fetch("SELECT * FROM content WHERE year=$1 AND type='kino'", int(query))
        except:
            pass
    elif "Janri" in stype:
        res = await conn.fetch("SELECT * FROM content WHERE genre ILIKE $1 AND type='kino'", f"%{query}%")
    else: 
        results = await conn.fetch(
            "SELECT id, name, parent_name, type, part_number FROM content "
            "WHERE name ILIKE $1 OR parent_name ILIKE $1 ORDER BY id DESC LIMIT 15", 
            f"%{query}%"
        )
        await conn.close()
        
        if not results:
            await m.answer("😔 Afsuski, ushbu nomga tegishli hech qanday kontent topilmadi.")
            return
            
        context_text = "🔍 <b>Topilgan natijalar:</b>\n\n"
        for item in results:
            if item['type'] == 'kino':
                context_text += f"🎬 <b>{item['name']}</b> — KODI: <code>{item['id']}</code>\n"
            elif item['type'] == 'part':
                context_text += f"📺 <b>{item['parent_name']} ({item['part_number']}-qism)</b> — KODI: <code>{item['id']}</code>\n"
            elif item['type'] == 'drama':
                context_text += f"🎭 <b>{item['parent_name']} ({item['part_number']}-qism)</b> — KODI: <code>{item['id']}</code>\n"
            elif item['type'] == 'multifilm':
                context_text += f"🧸 <b>{item['parent_name']} ({item['part_number']}-qism)</b> — KODI: <code>{item['id']}</code>\n"
        
        context_text += "\n🍿 Tomosha qilish uchun kerakli kino kodini shu yerga raqam shaklida yozib yuboring!"
        await m.answer(context_text, parse_mode="HTML")
        await state.set_state(UserStates.main)
        return

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
    sidebar_name = m.text.replace("📺 ", "")
    await state.update_data(current_ser=sidebar_name)
    await state.set_state(UserStates.viewing_parts)
    conn = await db_connect()
    count = await conn.fetchval("SELECT COUNT(*) FROM content WHERE parent_name=$1 AND type='part'", sidebar_name)
    await conn.close()
    kb = ReplyKeyboardBuilder()
    for i in range(1, count + 1, 10):
        end = min(i + 9, count)
        kb.button(text=f"🔢 {i}-{end} qismlar")
    kb.button(text="⬅️ Orqaga")
    kb.adjust(2)
    await m.answer(f"🎬 {sidebar_name} qismlari:", reply_markup=kb.as_markup(resize_keyboard=True))

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

# --- MULTIFILM QISMI ---
@dp.message(F.text == "🧸 Multifilmlar")
async def show_multis(m: types.Message, state: FSMContext, bot: Bot):
    if not await check_sub(m.from_user.id, bot):
        return await m.answer("⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'ling!", reply_markup=get_sub_keyboard())
    await state.set_state(UserStates.in_multifilm_list)
    conn = await db_connect()
    names = await conn.fetch("SELECT parent_name FROM content WHERE type='multifilm' GROUP BY parent_name ORDER BY MIN(id) ASC")
    await conn.close()
    kb = ReplyKeyboardBuilder()
    for row in names:
        kb.button(text=f"🧸 {row['parent_name']}")
    kb.button(text="⬅️ Bosh menyuga")
    kb.adjust(2)
    await m.answer("🧸 Multfilmni tanlang:", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text.startswith("🧸 "), UserStates.in_multifilm_list)
async def multi_parts_groups(m: types.Message, state: FSMContext):
    multi_name = m.text.replace("🧸 ", "")
    conn = await db_connect()
    # Multifilm bittalik formatda bo'lgani uchun to'g'ridan-to'g'ri uning videosini chiqaramiz
    res = await conn.fetchrow("SELECT * FROM content WHERE parent_name=$1 AND type='multifilm' LIMIT 1", multi_name)
    await conn.close()
    
    if res:
        caption = f"🧸 Multifilm: {res['parent_name']}\n🗣️ Tili: {res['lang']}\n🆔 Kod: {res['id']}{FOOTER_TEXT}"
        await m.answer_video(res['file_id'], caption=caption)
    else:
        await m.answer("❌ Kechirasiz, bu multfilmlarga tegishli video fayl topilmadi.")

# --- TO'G'RIDAN TO'G'RI KOD ORQALI QIDIRUV (MUKAMMAL VARIANT) ---
@dp.message(StateFilter(UserStates.main, None), F.text.isdigit())
async def direct_code_search(m: types.Message, state: FSMContext, bot: Bot):
    if not await check_sub(m.from_user.id, bot):
        return await m.answer("⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'ling!", reply_markup=get_sub_keyboard())
        
    code = int(m.text)
    conn = await db_connect()
    item = await conn.fetchrow("SELECT * FROM content WHERE id=$1", code)
    await conn.close()
    
    if item:
        if item['type'] == 'kino':
            caption = (f"🎬 Nomi: {item['name']}\n📆 Yili: {item['year']}\n🗣️ Tili: {item['lang']}\n"
                       f"🎭 Janri: {item['genre']}\n🌎 Davlati: {item['country']}\n🆔 Kod: {item['id']}{FOOTER_TEXT}")
            await m.answer_video(item['file_id'], caption=caption)
        elif item['type'] in ['part', 'drama', 'multifilm']:
            ctype = "📺 Serial" if item['type'] == 'part' else "🎭 Drama" if item['type'] == 'drama' else "🧸 Multifilm"
            
            # Agar multfilm bo'lsa qism raqamini ko'rsatish shart emas
            if item['type'] == 'multifilm':
                caption = f"{ctype}: {item['parent_name']}\n🗣️ Tili: {item['lang']}\n🆔 Kod: {item['id']}{FOOTER_TEXT}"
            else:
                caption = f"{ctype}: {item['parent_name']} | {item['part_number']}-qism\n🗣️ Tili: {item['lang']}\n🆔 Kod: {item['id']}{FOOTER_TEXT}"
                
            await m.answer_video(item['file_id'], caption=caption)
    else:
        await m.answer("😔 Afsuski, bu kod bilan hech qanday kontent topilmadi. Raqamni tekshirib qayta urinib ko'ring.")

# --- SMART BACK ---
@dp.message(F.text.in_(["⬅️ Orqaga", "⬅️ Bosh menyuga", "Orqaga", "Bosh menyuga"]))
async def universal_back(m: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    await state.set_state(UserStates.main)
    await m.answer("📋 Asosiy menyu:", reply_markup=main_menu(m.from_user.id))

# --- REKLAMA ---
@dp.message(F.text == "📢 Reklama", F.from_user.id.in_([ADMIN_ID, ADMIN_ID2]))
async def start_mail(m: types.Message, state: FSMContext):
    await m.answer("Xabarni kiriting:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Orqaga")]], resize_keyboard=True))
    await state.set_state(UserStates.waiting_mailing)

@dp.message(UserStates.waiting_mailing, F.from_user.id.in_([ADMIN_ID, ADMIN_ID2]))
async def broadcast(m: types.Message, state: FSMContext, bot: Bot):
    if m.text == "⬅️ Orqaga":
        await state.clear()
        await state.set_state(UserStates.main)
        await m.answer("Asosiy menyuga qaytildi:", reply_markup=main_menu(m.from_user.id))
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
    kb.button(text="🎭 Drama", callback_data="order_type:Drama")
    kb.button(text="🧸 Multifilm", callback_data="order_type:Multifilm")
    kb.adjust(2)
    await message.answer("Nimaga zakaz bermoqchisiz?", reply_markup=kb.as_markup())


# Botni ishga tushirish (Main logikasi oxiri)
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())