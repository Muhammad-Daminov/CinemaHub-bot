import asyncio
import json
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ================= CONFIG =================
TOKEN = "8683855893:AAH31bQzoIS-vHJyt0SdKi7AWGFZeBUzcQE"
ADMIN_ID = 6427415448

bot = Bot(token=TOKEN)
dp = Dispatcher()

DB_FILE = "movies.json"

admin_state = {}

# ================= MENU =================
menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📂 Kinolar")],
        [KeyboardButton(text="🔢 Kod orqali kino")],
        [KeyboardButton(text="⚙️ Admin panel")]
    ],
    resize_keyboard=True
)

# ================= DB =================
def load():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ================= START =================
@dp.message(F.text == "/start")
async def start(message: types.Message):
    await message.answer("🎬 Kino botga xush kelibsiz!", reply_markup=menu)

# ================= LIST =================
@dp.message(F.text == "📂 Kinolar")
async def list_movies(message: types.Message):
    data = load()

    if not data:
        return await message.answer("❌ Kino yo‘q")

    text = "\n".join([f"{k}. {v['name']}" for k, v in data.items()])
    await message.answer(text)

# ================= CODE =================
@dp.message(F.text == "🔢 Kod orqali kino")
async def ask_code(message: types.Message):
    await message.answer("🔢 Kino kodini kiriting:")

@dp.message(F.text.regexp(r"^\d+$"))
async def get_movie(message: types.Message):
    data = load()
    code = message.text

    if code in data:
        m = data[code]

        await message.answer_video(
            m["file_id"],
            caption=f"🎬 {m['name']}\n📆 {m['year']}\n🎭 {m['genre']}"
        )
    else:
        await message.answer("❌ Noto‘g‘ri kod")

# ================= ADMIN =================
@dp.message(F.text == "⚙️ Admin panel")
async def admin(message: types.Message):

    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Siz admin emassiz")

    await message.answer("➕ Kino qo‘shish uchun 'add' yozing")

# ================= ADD START =================
@dp.message(F.text == "add")
async def add_start(message: types.Message):

    if message.from_user.id != ADMIN_ID:
        return

    admin_state[message.from_user.id] = {"step": "name"}

    await message.answer("🎬 Kino nomini kiriting:")

# ================= ADD FLOW =================
@dp.message()
async def add_flow(message: types.Message):

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
    if state["step"] == "video":

        if not message.video:
            await message.answer("❌ Video yuboring")
            return

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

# ================= RUN =================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())