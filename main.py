import hmac, hashlib, aiohttp, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "ضع_توكنك_هنا"
SERVER = "https://gfdbgta.pythonanywhere.com"
SECRET_KEY = b"9fA7Q!mZx3E@H8K$2vD#CwLJ6T%R^yP&U*B4aS"

def sign(user_id, game):
    return hmac.new(
        SECRET_KEY,
        f"{user_id}:{game}".encode(),
        hashlib.sha256
    ).hexdigest()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 The Challenge", callback_data="thechallenge"),
         InlineKeyboardButton("🐔 Chicken Life", callback_data="chickenlife")]
    ])
    await update.message.reply_text("اختر اللعبة:", reply_markup=kb)

async def choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    game = q.data
    user_id = q.from_user.id

    payload = {
        "user_id": str(user_id),
        "game": game,
        "signature": sign(str(user_id), game)
    }

    async with aiohttp.ClientSession() as s:
        async with s.post(f"{SERVER}/authorize", json=payload) as r:
            data = await r.json()

    await q.message.reply_text(f"⬇ رابط التحميل:\n{SERVER}{data['url']}")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose))
app.run_polling()
