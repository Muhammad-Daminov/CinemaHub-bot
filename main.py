import asyncio
import os
import sys
import asyncpg
from dotenv import load_dotenv

# 1. .env faylini loyiha papkasidan aniq qidirish
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# 2. O'zgaruvchilarni yuklash
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Windows uchun maxsus event loop sozlamasi
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 3. Bot va Dispatcher obyektlarini yaratish
# Token None emasligini tekshirish (Local terminaldagi xatolikni oldini olish uchun)
if not BOT_TOKEN:
    raise ValueError("XATOLIK: BOT_TOKEN .env faylidan topilmadi!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Ma'lumotlar bazasi jadvalini yaratish funksiyasi (misol tariqasida)
async def create_table():
    if not DATABASE_URL:
        print("XATOLIK: DATABASE_URL topilmadi!")
        return
    
    try:
        # Baza ssilkasini terminalda tekshirish (ixtiyoriy)
        print(f"Ulanishga urinish: {DATABASE_URL}")
        
        conn = await asyncpg.connect(DATABASE_URL)
        # Bu yerga jadval yaratish SQL so'rovini yozishingiz mumkin
        # await conn.execute('''CREATE TABLE IF NOT EXISTS users(...)''')
        await conn.close()
        print("Bazaga ulanish muvaffaqiyatli yakunlandi.")
    except Exception as e:
        print(f"Bazaga ulanishda xatolik: {e}")

async def main():
    # Avval bazani tekshiramiz/yaratamiz
    await create_table()
    
    print("Bot polling rejimida ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot to'xtatildi")

BOT_TOKEN = os.getenv("BOT-TOKEN")
print(f"Ulanishga urinish: {BOT_TOKEN}") 
DATABASE_URL = os.getenv("DATABASE-URL")
print(f"Ulanishga urinish: {DATABASE_URL}") 



ADMIN_ID = 6427415448
CHANNEL = "@cinemahubb_HD"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

admin_state = {}

# ================= DB =================
async def create_table():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS movies (
        id SERIAL PRIMARY KEY,
        name TEXT,
        year INT,
        genre TEXT,
        file_id TEXT
    )
    """)
    await conn.close()


async def db():
    return await asyncpg.connect(DATABASE_URL)

# ================= MENU =================
menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📂 Kinolar")],
        [KeyboardButton(text="🔎 Qidirish")],
        [KeyboardButton(text="➕ Kino qo‘shish")]
    ],
    resize_keyboard=True
)

# ================= START =================
@dp.message(F.text == "/start")
async def start(m: types.Message):
    await m.answer("🎬 Kino botga xush kelibsiz!", reply_markup=menu)

# ================= LIST =================
@dp.message(F.text == "📂 Kinolar")
async def list_movies(m: types.Message):
    conn = await db()
    rows = await conn.fetch("SELECT * FROM movies ORDER BY id DESC")
    await conn.close()

    if not rows:
        return await m.answer("❌ Hech qanday kino yo‘q")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎬 {r['name']}", callback_data=f"m_{r['id']}")]
            for r in rows
        ]
    )

    await m.answer("🎬 Kinolar:", reply_markup=kb)

# ================= OPEN MOVIE =================
@dp.callback_query(F.data.startswith("m_"))
async def open_movie(c: types.CallbackQuery):
    movie_id = int(c.data.split("_")[1])

    conn = await db()
    m = await conn.fetchrow("SELECT * FROM movies WHERE id=$1", movie_id)
    await conn.close()

    if not m:
        return await c.answer("Topilmadi", show_alert=True)

    await c.message.answer_video(
        m["file_id"],
        caption=f"🎬 {m['name']}\n📆 {m['year']}\n🎭 {m['genre']}"
    )

# ================= ADD START =================
@dp.message(F.text == "➕ Kino qo‘shish")
async def add_start(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("❌ Ruxsat yo‘q")

    admin_state[m.from_user.id] = {"step": "name"}
    await m.answer("🎬 Kino nomini kiriting:")

# ================= FLOW =================
@dp.message()
async def flow(m: types.Message):
    uid = m.from_user.id

    if uid not in admin_state:
        return

    st = admin_state[uid]

    if st["step"] == "name":
        st["name"] = m.text
        st["step"] = "year"
        return await m.answer("📆 Yilni kiriting:")

    if st["step"] == "year":
        if not m.text.isdigit():
            return await m.answer("❌ Yil faqat raqam bo‘lishi kerak")
        st["year"] = int(m.text)
        st["step"] = "genre"
        return await m.answer("🎭 Janr:")

    if st["step"] == "genre":
        st["genre"] = m.text
        st["step"] = "video"
        return await m.answer("🎬 Video yuboring:")

    if st["step"] == "video":
        if not m.video:
            return await m.answer("❌ Video yuboring")

        conn = await db()
        row = await conn.fetchrow("""
            INSERT INTO movies(name, year, genre, file_id)
            VALUES($1,$2,$3,$4)
            RETURNING id
        """, st["name"], st["year"], st["genre"], m.video.file_id)
        await conn.close()

        admin_state.pop(uid)

        await m.answer(f"✅ Saqlandi!\n🎟 Kod: {row['id']}")

# ================= SEARCH =================
@dp.message(F.text == "🔎 Qidirish")
async def ask(m: types.Message):
    await m.answer("🔎 Kino nomi yoki janr yozing:")

@dp.message()
async def search(m: types.Message):
    conn = await db()

    rows = await conn.fetch("""
        SELECT * FROM movies
        WHERE name ILIKE $1 OR genre ILIKE $1
    """, f"%{m.text}%")

    await conn.close()

    if not rows:
        return await m.answer("❌ Topilmadi")

    text = "\n".join([f"{r['id']}. {r['name']}" for r in rows])
    await m.answer(text)

# ================= RUN =================
async def main():
    await create_table()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())