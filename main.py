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

# ✅ إعداد Flask لتشغيل السيرفر على Replit
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

# ✅ إعدادات البوت
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"
ADMIN_CHAT_ID = 1077911771

pending_payments = {}
approved_users = {}

# ✅ رسالة /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 أهلاً بك في بوت تحميل اللعبة!\n\n"
        "💳 <b>طريقة الدفع:</b>\n"
        " تحويل المبلغ إلى بطاقة <b>ماستر كارد</b>:\n"
        "<code>7113282938</code>\n\n"
        "⚠️ المبلغ غير محدد، لكن يجب الدفع أولاً.\n"
        "⚠️ أقل مبلغ للدفع هو IQD 1000.\n\n"
        "📩 بعد الدفع، أرسل صورة إيصال الدفع هنا.\n"
        "⚠️ تنبيه مهم:\n"
        "اللعبة متاحة الآن فقط على أجهزة الأندرويد.\n"
        "📞 للتواصل أو الدعم: <a href='https://www.instagram.com/ta_smg'>اضغط هنا للتواصل عبر إنستغرام</a>"
    )
    await update.message.reply_text(welcome, parse_mode="HTML")

# ✅ استقبال صورة الإيصال
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

# ✅ إدارة الرد على الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        data = query.data

        if data.startswith("approve_"):
            user_id = int(data.split("_")[1])
            if user_id in pending_payments:
                user = await context.bot.get_chat(user_id)
                username = user.full_name
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                approved_users[user_id] = {
                    'approved_time': time.time(),
                    'status': 'approved'
                }
                del pending_payments[user_id]

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📱 أندرويد", callback_data=f"device_android_{user_id}"),
                        InlineKeyboardButton("🍎 آيفون", callback_data=f"device_ios_{user_id}")
                    ]
                ])

                await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ تم قبول الدفع بنجاح!\n\n📲 يرجى اختيار نوع جهازك لتحصل على رابط التحميل:",
                    reply_markup=keyboard
                )

                await query.edit_message_caption(
                    f"✅ تم قبول الدفع.\n"
                    f"👤 الاسم: {username}\n"
                    f"🆔 المعرف: {user_id}\n"
                    f"⏰ الوقت: {now_str}\n"
                    "المستخدم في انتظار اختيار نوع الجهاز."
                )
            else:
                await query.edit_message_caption("⚠️ لم يتم العثور على إيصال لهذا المستخدم.")

        elif data.startswith("reject_"):
            user_id = int(data.split("_")[1])
            if user_id in pending_payments:
                user = await context.bot.get_chat(user_id)
                username = user.full_name
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                del pending_payments[user_id]

                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ تم رفض إيصال الدفع.\n\n🔍 يرجى التحقق من المعلومات أو التواصل معنا:\n📱 https://www.instagram.com/ta_smg"
                )

                await query.edit_message_caption(
                    f"🚫 تم رفض الدفع.\n"
                    f"👤 الاسم: {username}\n"
                    f"🆔 المعرف: {user_id}\n"
                    f"⏰ الوقت: {now_str}"
                )
            else:
                await query.edit_message_caption("⚠️ لم يتم العثور على إيصال لهذا المستخدم.")

        elif data.startswith("device_"):
            _, device_code, user_id = data.split("_")
            user_id = int(user_id)

            if user_id not in approved_users:
                await context.bot.send_message(chat_id=user_id, text="❌ لم يتم الموافقة على الدفع.")
                return

            # ✅ رابط التحميل المؤقت
            download_url = "https://pixeldrain.com/api/file/E5iLBCRv?download"
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔗 رابط التحميل:\n{download_url}\n\n⚠️ صالح للتحميل لمرة واحدة فقط خلال 10 ثواني."
            )

            del approved_users[user_id]

    except Exception as e:
        print(f"❌ خطأ في button_handler: {e}")
        try:
            await query.edit_message_caption("❌ حدث خطأ أثناء معالجة الطلب.")
        except:
            pass

# ✅ تشغيل البوت
async def main():
    try:
        application = ApplicationBuilder().token(TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        application.add_handler(CallbackQueryHandler(button_handler))

        print("🤖 البوت يعمل الآن...")
        await application.run_polling(drop_pending_updates=True)
    except Exception as e:
        print(f"❌ خطأ في تشغيل البوت: {e}")

# ✅ تشغيل السيرفر والبوت
if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))

        keep_alive()
        nest_asyncio.apply()

        print("🚀 بدء تشغيل البوت...")
        asyncio.run(main())

    except KeyboardInterrupt:
        print("🛑 تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        print(f"❌ خطأ عام: {e}")
