#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Bot - Game Distribution
"""

import hmac
import hashlib
import logging
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# =========================
# CONFIG
# =========================
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"
SERVER_HOST = "http://localhost:5000"
SECRET_KEY = b"CHANGE_THIS_SECRET_KEY"

GAME_NAMES = {
    "thechallenge": "The Challenge",
    "chickenlife": "Chicken Life"
}

INSTAGRAM_SUPPORT = "@your_instagram"

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
    kb = [
        [InlineKeyboardButton(GAME_NAMES["thechallenge"], callback_data="thechallenge")],
        [InlineKeyboardButton(GAME_NAMES["chickenlife"], callback_data="chickenlife")]
    ]
    await update.message.reply_text(
        "🎮 اختر اللعبة:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 الأسعار:\n"
        "The Challenge - 10$\n"
        "Chicken Life - 8$\n\n"
        "📞 للشراء استخدم /support"
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📞 الدعم:\nInstagram: {INSTAGRAM_SUPPORT}\n"
        f"🆔 ID الخاص بك:\n{update.effective_user.id}"
    )

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = str(q.from_user.id)
    game = q.data
    signature = sign(user_id, game)

    payload = {
        "user_id": user_id,
        "game": game,
        "signature": signature
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{SERVER_HOST}/authorize", json=payload) as r:
            if r.status != 200:
                await q.message.reply_text("❌ فشل إنشاء الرابط")
                return

            data = await r.json()
            await q.message.reply_text(
                f"✅ رابط التحميل:\n{data['url']}\n\n"
                "⚠️ يعمل على جهازك فقط"
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
