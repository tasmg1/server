#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hmac
import hashlib
import logging
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# =========================
# CONFIG
# =========================
TOKEN = "7483920011:AAZP9M8R_5fQyVJmL1kN0xXcA7SDeE"
SERVER_HOST = "http://localhost:5000"
SECRET_KEY = b"c9F7@A1e#Qx8!LZ2%0M^dK$B4W*H3Jp"

GAME_NAMES = {
    "thechallenge": "The Challenge",
    "chickenlife": "Chicken Life"
}

INSTAGRAM_SUPPORT = "https://www.instagram.com/p1ay.zone"

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# =========================
# SECURITY
# =========================
def sign(user_id, game):
    msg = f"{user_id}:{game}".encode()
    return hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()

# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(GAME_NAMES["thechallenge"], callback_data="thechallenge")],
        [InlineKeyboardButton(GAME_NAMES["chickenlife"], callback_data="chickenlife")]
    ]

    await update.message.reply_text(
        "🎮 مرحباً بك في Play Zone\n\nاختر اللعبة:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 الأسعار:\nThe Challenge — 1000 IQD\nChicken Life — 1000 IQD\n\n/support للدفع"
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📞 الدعم:\n{INSTAGRAM_SUPPORT}\n\nID:\n{update.effective_user.id}",
        disable_web_page_preview=True
    )

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    payload = {
        "user_id": str(query.from_user.id),
        "game": query.data,
        "signature": sign(str(query.from_user.id), query.data)
    }

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{SERVER_HOST}/authorize", json=payload) as r:
            if r.status != 200:
                await query.message.reply_text("❌ فشل إنشاء الرابط")
                return

            data = await r.json()
            await query.message.reply_text(
                f"✅ رابط التحميل:\n{data['url']}",
                disable_web_page_preview=True
            )

# =========================
# MAIN
# =========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("payment", payment))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CallbackQueryHandler(choose_game))
    app.run_polling()

if __name__ == "__main__":
    main()
