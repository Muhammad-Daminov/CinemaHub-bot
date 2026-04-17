import asyncio
import json
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ================= CONFIG (DEPLOY SAFE) =================
TOKEN = "8683855893:AAH31bQzoIS-vHJyt0SdKi7AWGFZeBUzcQE"
CHANNEL = "@cinemahubb_HD"
ADMIN_ID = 6427415448

bot = Bot(token=TOKEN)
dp = Dispatcher()

JSON_FILE = "movies.json"

admin_state = {}
user_state = {}

# ================= MENU =================
menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📂 Kinolar ro‘yxati")],
        [KeyboardButton(text="🎬 Kino nomi bilan qidirish")],
        [KeyboardButton(text="🔢 Kod orqali qidirish")],
        [KeyboardButton(text="⚙️ Admin panel")]
    ],
    resize_keyboard=True
)

# ================= DB =================
def load():
    if not os.path.exists(JSON_FILE):
        return {}
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save(data):
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ================= SUB CHECK =================
async def is_sub(user_id):
    try:
        m = await bot.get_chat_member(CHANNEL, user_id)
        return m.status in ["member", "administrator", "creator"]
    except:
        return False

# ================= START =================
@dp.message(F.text == "/start")
async def start(message: types.Message):
    if not await is_sub(message.from_user.id):
        return await message.answer("❌ Kanalga obuna bo‘lmagansiz")

    await message.answer("🎬 CinemaHub botga xush kelibsiz!", reply_markup=menu)

# ================= MOVIE LIST =================
def movies_menu(data):
    kb = []
    for mid, m in data.items():
        kb.append([
            InlineKeyboardButton(
                text=f"🎬 {m['name']}",
                callback_data=f"movie_{mid}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(F.text == "📂 Kinolar ro‘yxati")
async def list_movies(message: types.Message):
    data = load()
    if not data:
        return await message.answer("❌ kino yo‘q")

    await message.answer("🎬 tanlang:", reply_markup=movies_menu(data))

# ================= SHOW MOVIE =================
@dp.callback_query(F.data.startswith("movie_"))
async def show(call: types.CallbackQuery):
    mid = call.data.split("_")[1]
    data = load()

    if mid not in data:
        return await call.answer("❌ topilmadi", show_alert=True)

    m = data[mid]

    caption = f"""🎬 <b>{m['name']}</b>

📆 {m['year']}
🎭 {m['genre']}
🔢 {mid}"""

    await call.message.answer_video(
        m["file_id"],
        caption=caption,
        parse_mode="HTML"
    )

    await call.answer()

# ================= SEARCH NAME =================
@dp.message(F.text == "🎬 Kino nomi bilan qidirish")
async def ask_name(message: types.Message):
    user_state[message.from_user.id] = "name"
    await message.answer("🔍 kino nomini yozing")

@dp.message()
async def search(message: types.Message):
    uid = message.from_user.id
    data = load()

    if user_state.get(uid) == "name":
        for mid, m in data.items():
            if message.text.lower() in m["name"].lower():

                caption = f"""🎬 <b>{m['name']}</b>

📆 {m['year']}
🎭 {m['genre']}
🔢 {mid}"""

                await message.answer_video(
                    m["file_id"],
                    caption=caption,
                    parse_mode="HTML"
                )
                return

        return await message.answer("❌ topilmadi")

    if message.text.isdigit():
        if message.text in data:
            m = data[message.text]

            caption = f"""🎬 <b>{m['name']}</b>

📆 {m['year']}
🎭 {m['genre']}
🔢 {message.text}"""

            await message.answer_video(
                m["file_id"],
                caption=caption,
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ kod yo‘q")

# ================= ADMIN =================
@dp.message(F.text == "⚙️ Admin panel")
async def admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ admin emassiz")

    await message.answer("⚙️ Admin panel ishlayapti")

# ================= RUN =================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())