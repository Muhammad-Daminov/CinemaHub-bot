import asyncio
import json
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ================= CONFIG =================
TOKEN = "8683855893:AAH31bQzoIS-vHJyt0SdKi7AWGFZeBUzcQE"
CHANNEL = "@cinemahubb_HD"
ADMIN_ID = 6427415448

if not TOKEN:
    raise Exception("BOT_TOKEN topilmadi!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

JSON_FILE = "movies.json"

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

# ================= START =================
@dp.message(F.text == "/start")
async def start(message: types.Message):
    await message.answer("🎬 CinemaHub botga xush kelibsiz!", reply_markup=menu)

# ================= MOVIE LIST =================
def movies_menu(data):
    kb = []
    for mid, m in data.items():
        kb.append([
            InlineKeyboardButton(
                text=f"🎬 {m['name']}",
                callback_data=f"movie:{mid}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(F.text == "📂 Kinolar ro‘yxati")
async def list_movies(message: types.Message):
    data = load()
    if not data:
        return await message.answer("❌ Hozircha kino yo‘q.")

    await message.answer("🎬 Kinolar:", reply_markup=movies_menu(data))

# ================= SHOW MOVIE =================
@dp.callback_query(F.data.startswith("movie:"))
async def show_movie(call: types.CallbackQuery):
    mid = call.data.split(":")[1]
    data = load()

    if mid not in data:
        return await call.answer("❌ Kino topilmadi", show_alert=True)

    m = data[mid]

    caption = f"""🎬 <b>{m['name']}</b>
📆 {m['year']}
🎭 {m['genre']}
🔢 Kod: {mid}"""

    await call.message.answer_video(
        m["file_id"],
        caption=caption,
        parse_mode="HTML"
    )

    await call.answer()

# ================= SEARCH =================
@dp.message(F.text == "🎬 Kino nomi bilan qidirish")
async def ask_name(message: types.Message):
    user_state[message.from_user.id] = "name"
    await message.answer("🔍 Kino nomini kiriting:")

@dp.message(F.text == "🔢 Kod orqali qidirish")
async def ask_code(message: types.Message):
    user_state[message.from_user.id] = "code"
    await message.answer("🔢 Kino kodini yuboring:")

@dp.message()
async def handler(message: types.Message):
    data = load()
    uid = message.from_user.id

    # NAME SEARCH
    if user_state.get(uid) == "name":
        for mid, m in data.items():
            if message.text.lower() in m["name"].lower():
                await message.answer_video(m["file_id"], caption=m["name"])
                return
        return await message.answer("❌ Kino topilmadi.")

    # CODE SEARCH
    if user_state.get(uid) == "code":
        if message.text in data:
            m = data[message.text]
            await message.answer_video(m["file_id"], caption=m["name"])
        else:
            await message.answer("❌ Bunday kod mavjud emas.")
        return

# ================= ADMIN PANEL =================
admin_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="➕ Kino qo‘shish", callback_data="add_movie")],
        [InlineKeyboardButton(text="📂 Kinolar soni", callback_data="admin_list")]
    ]
)

@dp.message(F.text == "⚙️ Admin panel")
async def admin_panel(message: types.Message):

    if message.from_user.id != ADMIN_ID:
        return await message.answer(
            "❌ Siz admin emassiz yoki ruxsatingiz yo‘q."
        )

    await message.answer("⚙️ Admin panel:", reply_markup=admin_kb)

# ================= ADMIN ACTIONS =================
@dp.callback_query(F.data == "add_movie")
async def add_movie(call: types.CallbackQuery):

    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo‘q", show_alert=True)

    await call.message.answer(
        "🎬 Kino qo‘shish:\n"
        "Video yuboring va quyidagicha yozing:\n"
        "ID | NAME | YEAR | GENRE"
    )

    await call.answer()

@dp.callback_query(F.data == "admin_list")
async def admin_list(call: types.CallbackQuery):

    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo‘q", show_alert=True)

    data = load()
    await call.message.answer(f"📂 Jami kinolar: {len(data)}")
    await call.answer()

# ================= RUN =================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())