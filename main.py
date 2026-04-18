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
ADMIN_ID = 6427415448

if not TOKEN:
    raise Exception("BOT_TOKEN topilmadi!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

JSON_FILE = "movies.json"

# ================= STATES =================
admin_state = {}

# ================= MENU =================
menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📂 Kinolar ro‘yxati")],
        [KeyboardButton(text="🔢 Kod orqali kino")],
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
    await message.answer("🎬 Kino botga xush kelibsiz!", reply_markup=menu)

# ================= MOVIE LIST =================
def movie_buttons(data):
    kb = []
    for mid, m in data.items():
        kb.append([
            InlineKeyboardButton(
                text=m["name"],
                callback_data=f"movie:{mid}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(F.text == "📂 Kinolar ro‘yxati")
async def list_movies(message: types.Message):
    data = load()
    if not data:
        return await message.answer("❌ Kino yo‘q")

    await message.answer("🎬 Kinolar:", reply_markup=movie_buttons(data))

# ================= SHOW MOVIE =================
@dp.callback_query(F.data.startswith("movie:"))
async def show_movie(call: types.CallbackQuery):
    mid = call.data.split(":")[1]
    data = load()

    if mid not in data:
        return await call.answer("❌ topilmadi", show_alert=True)

    m = data[mid]

    caption = f"""🎬 {m['name']}
📆 {m['year']}
🎭 {m['genre']}
🔢 Kod: {mid}"""

    await call.message.answer_video(m["file_id"], caption=caption)
    await call.answer()

# ================= CODE SEARCH =================
@dp.message(F.text == "🔢 Kod orqali kino")
async def ask_code(message: types.Message):
    await message.answer("🔢 Kino kodini yuboring:")

@dp.message()
async def code_handler(message: types.Message):
    data = load()
    text = message.text.strip()

    if text.isdigit():
        if text in data:
            m = data[text]
            await message.answer_video(
                m["file_id"],
                caption=f"{m['name']}"
            )
        else:
            await message.answer("❌ Siz noto‘g‘ri ma’lumot kiritdingiz.")

# ================= ADMIN PANEL =================
admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Kino qo‘shish")],
        [KeyboardButton(text="📂 Kinolar soni")],
        [KeyboardButton(text="🔙 Chiqish")]
    ],
    resize_keyboard=True
)

@dp.message(F.text == "⚙️ Admin panel")
async def admin_panel(message: types.Message):

    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Siz admin emassiz.")

    await message.answer("⚙️ Admin panel:", reply_markup=admin_kb)

# ================= ADD MOVIE FLOW =================
@dp.message(F.text == "➕ Kino qo‘shish")
async def add_start(message: types.Message):

    if message.from_user.id != ADMIN_ID:
        return

    admin_state[message.from_user.id] = {"step": "name"}
    await message.answer("🎬 Kino nomini kiriting:")

@dp.message()
async def admin_flow(message: types.Message):

    uid = message.from_user.id

    if uid not in admin_state:
        return

    state = admin_state[uid]

    # NAME
    if state["step"] == "name":
        state["name"] = message.text
        state["step"] = "year"
        await message.answer("📆 Yilni kiriting:")
        return

    # YEAR
    if state["step"] == "year":
        state["year"] = message.text
        state["step"] = "genre"
        await message.answer("🎭 Janrni kiriting:")
        return

    # GENRE
    if state["step"] == "genre":
        state["genre"] = message.text
        state["step"] = "video"
        await message.answer("🎬 Video yuboring:")
        return

    # VIDEO
    if message.video and state["step"] == "video":

        state["file_id"] = message.video.file_id

        data = load()
        movie_id = str(len(data) + 1)

        data[movie_id] = {
            "name": state["name"],
            "year": state["year"],
            "genre": state["genre"],
            "file_id": state["file_id"]
        }

        save(data)

        admin_state.pop(uid)

        await message.answer("✅ Kino qo‘shildi!")
        return

# ================= RUN =================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())