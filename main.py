import os
import sys
import time
import signal
import asyncio
import aiohttp
import nest_asyncio
from datetime import datetime
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# ========================
# Flask keep alive
app = Flask('')

@app.route('/')
def home():
    return "✅ Bot is alive and running!"

def run():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# ========================
# إعدادات البوت
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"
ADMIN_CHAT_ID = 1077911771

pending_payments = {}
approved_users = {}

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 أهلاً بك في بوت تحميل الألعاب!\n\n"
        "⚠️ التحميل بعد الدفع:\n"
        "1️⃣ The Challenge\n"
        "2️⃣ Chicken Life\n\n"
        "💳 <b>طريقة الدفع:</b>\n"
        " تحويل المبلغ إلى بطاقة <b>ماستر كارد</b>:\n"
        "<code>7113282938</code>\n\n"
        "⚠️ المبلغ غير محدد، لكن يجب الدفع أولاً.\n"
        "⚠️ أقل مبلغ للدفع هو IQD 1000.\n\n"
        "📩 بعد الدفع، أرسل صورة إيصال الدفع هنا.\n"
        "⚠️ الألعاب متاحة فقط على أجهزة الأندرويد حالياً.\n"
        "📞 للتواصل أو الدعم: <a href='https://instagram.com/p1ay.zone'>اضغط هنا للتواصل عبر إنستغرام</a>"
    )
    await update.message.reply_text(welcome, parse_mode="HTML")

# استقبال صورة الإيصال
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    file_id = update.message.photo[-1].file_id
    pending_payments[user_id] = file_id

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")
        ]
    ])

    await context.bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=file_id,
        caption=f"مراجعة إيصال دفع من المستخدم: {user_id}",
        reply_markup=keyboard
    )

    await update.message.reply_text("📩 تم استلام الإيصال وسيتم مراجعته قريبًا.")

# الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("approve_"):
        user_id = int(data.split("_")[1])
        approved_users[user_id] = True
        del pending_payments[user_id]

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎮 The Challenge", callback_data=f"game_thechallenge_{user_id}"),
                InlineKeyboardButton("🐔 Chicken Life", callback_data=f"game_chickenlife_{user_id}")
            ]
        ])

        await context.bot.send_message(
            chat_id=user_id,
            text="✅ تم قبول الدفع بنجاح!\n\n🎯 اختر اللعبة التي تريد تحميلها:",
            reply_markup=keyboard
        )

        await query.edit_message_caption("✅ تم قبول الدفع.")

    elif data.startswith("reject_"):
        user_id = int(data.split("_")[1])
        del pending_payments[user_id]
        await context.bot.send_message(chat_id=user_id, text="❌ تم رفض إيصال الدفع.")

    elif data.startswith("game_"):
        _, game, user_id = data.split("_")
        user_id = int(user_id)

        payload = {"game": game}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://gfdbgta.pythonanywhere.com/generate_link",
                json=payload
            ) as resp:
                result = await resp.json()
                url = result.get("download_url")

                if url:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🔗 رابط التحميل:\n{url}\n\n⚠️ صالح لمدة 30 ثانية فقط."
                    )
                    del approved_users[user_id]
                else:
                    await context.bot.send_message(chat_id=user_id, text="❌ فشل توليد رابط التحميل.")

# تشغيل البوت
async def main():
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_handler))
    await application.run_polling()

if __name__ == "__main__":
    keep_alive()
    nest_asyncio.apply()
    asyncio.run(main())
