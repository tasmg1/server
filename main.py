# ========================
# الاستيرادات
# ========================
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

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# ========================
# سيرفر صغير لإبقاء البوت حي
# ========================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot is alive and running!"

def run_flask():
    app.run(
        host='0.0.0.0',
        port=8080,
        debug=False,
        use_reloader=False
    )

def keep_alive():
    thread = Thread(target=run_flask)
    thread.daemon = True
    thread.start()

# ========================
# إعدادات البوت
# ========================
TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"
ADMIN_CHAT_ID = 1077911771

pending_payments = {}   # user_id -> file_id
approved_users = {}    # user_id -> True

# ========================
# أمر /start
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "👋 أهلاً بك في بوت تحميل الألعاب!\n\n"
        "⚠️ التحميل بعد الدفع:\n"
        "1️⃣ The Challenge\n"
        "2️⃣ Chicken Life\n\n"
        "💳 <b>طريقة الدفع:</b>\n"
        "تحويل المبلغ إلى بطاقة <b>ماستر كارد</b>:\n"
        "<code>7113282938</code>\n\n"
        "⚠️ أقل مبلغ للدفع هو IQD 1000.\n\n"
        "📩 بعد الدفع، أرسل صورة إيصال الدفع هنا.\n"
        "⚠️ الألعاب متاحة فقط على أجهزة الأندرويد حالياً."
    )
    await update.message.reply_text(welcome_message, parse_mode="HTML")

# ========================
# استقبال صورة إيصال الدفع
# ========================
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
        caption=f"📩 إيصال دفع من المستخدم: {user_id}",
        reply_markup=keyboard
    )

    await update.message.reply_text(
        "📩 تم استلام الإيصال وسيتم مراجعته قريبًا."
    )

# ========================
# معالجة الأزرار
# ========================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    # -------- قبول الدفع --------
    if data.startswith("approve_"):
        user_id = int(data.split("_")[1])

        approved_users[user_id] = True
        pending_payments.pop(user_id, None)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎮 The Challenge",
                    callback_data=f"game_thechallenge_{user_id}"
                ),
                InlineKeyboardButton(
                    "🐔 Chicken Life",
                    callback_data=f"game_chickenlife_{user_id}"
                )
            ]
        ])

        await context.bot.send_message(
            chat_id=user_id,
            text="✅ تم قبول الدفع!\nاختر اللعبة:",
            reply_markup=keyboard
        )

    # -------- اختيار اللعبة --------
    elif data.startswith("game_"):
        _, game_name, user_id = data.split("_")
        user_id = int(user_id)

        payload = {"game": game_name}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://gfdbgta.pythonanywhere.com/generate_link",
                json=payload
            ) as response:

                result = await response.json()
                download_link = result.get("download_url")

                if download_link:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "🔗 رابط التحميل:\n"
                            f"{download_link}\n\n"
                            "⚠️ صالح لمدة 30 ثانية فقط."
                        )
                    )
                else:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="❌ فشل توليد رابط التحميل."
                    )

# ========================
# تشغيل البوت
# ========================
async def main():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 البوت يعمل...")
    await application.run_polling()

# ========================
# نقطة التشغيل
# ========================
if __name__ == "__main__":
    keep_alive()
    nest_asyncio.apply()
    asyncio.run(main())
