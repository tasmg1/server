#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import hmac
import hashlib
import asyncio
import aiohttp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# =====================
# CONFIGURATION
# =====================
TOKEN = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # ضع توكن البوت هنا
SERVER_HOST = "https://gfdbgta.pythonanywhere.com"  # رابط السيرفر
SECRET_KEY = b"Z9@kP7#X!m2A^S4Q%H8FJ$D0L&N"  # نفس مفتاح السيرفر
INSTAGRAM_SUPPORT = "@p1ay.zone"

GAME_NAMES = {
    "thechallenge": "The Challenge",
    "chickenlife": "Chicken Life"
}

# =====================
# LOGGING
# =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# =====================
# SECURITY
# =====================
def sign(user_id: str, game: str) -> str:
    message = f"{user_id}:{game}".encode('utf-8')
    return hmac.new(SECRET_KEY, message, hashlib.sha256).hexdigest()

# =====================
# COMMAND HANDLERS
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton(GAME_NAMES["thechallenge"], callback_data="thechallenge")],
        [InlineKeyboardButton(GAME_NAMES["chickenlife"], callback_data="chickenlife")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_message = (
        f"👋 أهلاً بك *{user.first_name}* في بوت تحميل الألعاب!\n\n"
        "🎮 اختر اللعبة التي تريد تحميلها:"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown", reply_markup=reply_markup)

async def payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "💳 معلومات الدفع:\n"
        "• The Challenge - 1000 IQD\n"
        "• Chicken Life - 1000 IQD\n"
        f"للدعم: {INSTAGRAM_SUPPORT}"
    )
    await update.message.reply_text(message, parse_mode="Markdown")

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        f"📞 تواصل معنا عبر Instagram: {INSTAGRAM_SUPPORT}"
    )
    await update.message.reply_text(message, parse_mode="Markdown")

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    game = query.data
    user = query.from_user
    user_id = str(user.id)
    signature = sign(user_id, game)
    payload = {"user_id": user_id, "game": game, "signature": signature}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{SERVER_HOST}/authorize", json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    download_url = f"{SERVER_HOST}{data['url']}"
                    await query.message.reply_text(
                        f"✅ رابط التحميل:\n{download_url}\n⚠️ يعمل على جهازك فقط", parse_mode="Markdown"
                    )
                else:
                    await query.message.reply_text("❌ خطأ في توليد الرابط. استخدم /support")
    except Exception as e:
        await query.message.reply_text("❌ حدث خطأ. حاول مرة أخرى أو استخدم /support")

# =====================
# MAIN
# =====================
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("payment", payment))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CallbackQueryHandler(choose_game))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
