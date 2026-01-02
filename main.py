import asyncio
import aiohttp
import hmac
import hashlib
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# =========================
# CONFIGURATION
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"
SERVER_HOST = "https://gfdbgta.pythonanywhere.com"  # رابط السيرفر عند النشر
SECRET_KEY = b"ta_smg#F9!KX7@R2$wZ%M8^"

# =========================
# DATABASE SETUP
db = sqlite3.connect("db.sqlite", check_same_thread=False)
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    game TEXT,
    device_id TEXT,
    downloads INTEGER DEFAULT 0
)
""")
db.commit()

# =========================
# HELPERS
def sign(user_id, game):
    return hmac.new(SECRET_KEY, f"{user_id}:{game}".encode(), hashlib.sha256).hexdigest()

# =========================
# TELEGRAM BOT HANDLERS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 The Challenge", callback_data="thechallenge")],
        [InlineKeyboardButton("🐔 Chicken Life", callback_data="chickenlife")]
    ])
    await update.message.reply_text(
        "👋 أهلاً بك في بوت تحميل الألعاب!\n\n"
        "⚠️ حالياً، الألعاب متوفرة فقط على أجهزة *الأندرويد*.\n"
        "⚠️ التحميل مرتبط بجهازك فقط.\n"
        "💳 بعد الدفع، اختر اللعبة لتحصل على الرابط.\n"
        "📱 الرابط لن يعمل على جهاز آخر.\n\n"
        "⬇ اختر اللعبة التي تريد تحميلها:",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    game = query.data
    user_id = str(query.from_user.id)

    cur = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        db.execute("INSERT INTO users(user_id, game) VALUES (?,?)", (user_id, game))
        db.commit()

    payload = {"user_id": user_id, "game": game, "signature": sign(user_id, game)}

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{SERVER_HOST}/authorize", json=payload) as r:
            data = await r.json()

    await query.message.reply_text(
        f"⬇ رابط تحميل لعبتك:\n{SERVER_HOST}{data['url']}\n\n⚠️ الرابط مرتبط بجهازك فقط."
    )

# =========================
# MAIN
if __name__ == "__main__":
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CallbackQueryHandler(choose_game))
    print("🤖 البوت يعمل الآن...")
    asyncio.run(app_bot.run_polling(drop_pending_updates=True))
