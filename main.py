#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Bot - Secure Game Distribution
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
# CONFIG (STRONG TOKENS)
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
        "🎮 *مرحباً بك في Play Zone*\n\n"
        "اختر اللعبة التي تريد تحميلها:\n"
        "⚠️ الرابط يعمل على جهازك فقط",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 *الأسعار*\n\n"
        "🎮 The Challenge — 1000 IQD\n"
        "🐔 Chicken Life — 1000 IQD\n\n"
        "للدفع والتفعيل استخدم /support",
        parse_mode="Markdown"
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 *الدعم الفني*\n\n"
        f"📱 Instagram:\n{INSTAGRAM_SUPPORT}\n\n"
        "🆔 أرسل لنا هذا المعرف:\n"
        f"`{update.effective_user.id}`",
        parse_mode="Markdown"
    )

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    game = query.data
    signature = sign(user_id, game)

    payload = {
        "user_id": user_id,
        "game": game,
        "signature": signature
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{SERVER_HOST}/authorize", json=payload) as response:
            if response.status != 200:
                await query.message.reply_text(
                    "❌ فشل إنشاء الرابط.\n"
                    "يرجى التواصل مع الدعم."
                )
                return

            data = await response.json()
            await query.message.reply_text(
                f"✅ *رابط التحميل*\n\n"
                f"{data['url']}\n\n"
                "⚠️ يعمل على جهازك فقط",
                parse_mode="Markdown"
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
