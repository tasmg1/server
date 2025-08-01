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
# تشغيل سيرفر Flask بسيط للحفاظ على البوت حي (مثلاً على Replit)
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
TOKEN = "YOUR_BOT_TOKEN_HERE"
ADMIN_CHAT_ID = 1077911771

pending_payments = {}
approved_users = {}

# رسالة /start مع شرح الدفع واللعبتين
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 أهلاً بك في بوت تحميل الألعاب!\n\n"
        "⚠️ لدينا لعبتان متاحتان للتحميل بعد الدفع:\n"
        "1️⃣ The Challenge (اللعبة القديمة)\n"
        "2️⃣ Chicken Life (اللعبة الجديدة)\n\n"
        "💳 <b>طريقة الدفع:</b>\n"
        " تحويل المبلغ إلى بطاقة <b>ماستر كارد</b>:\n"
        "<code>7113282938</code>\n\n"
        "⚠️ المبلغ غير محدد، لكن يجب الدفع أولاً.\n"
        "⚠️ أقل مبلغ للدفع هو IQD 1000.\n\n"
        "📩 بعد الدفع، أرسل صورة إيصال الدفع هنا.\n"
        "⚠️ الألعاب الآن متاحة على أجهزة الأندرويد فقط !\n"
        "📞 للتواصل أو الدعم: <a href='https://www.instagram.com/ta_smg'>اضغط هنا للتواصل عبر إنستغرام</a>"
    )
    await update.message.reply_text(welcome, parse_mode="HTML")

# استقبال صورة الدفع
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

# التعامل مع أزرار البوت
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        data = query.data

        # قبول الدفع
        if data.startswith("approve_"):
            user_id = int(data.split("_")[1])
            if user_id in pending_payments:
                user = await context.bot.get_chat(user_id)
                username = user.full_name
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                approved_users[user_id] = {'approved_time': time.time(), 'status': 'approved'}
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

        # رفض الدفع
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

        # اختيار الجهاز
        elif data.startswith("device_"):
            _, device_code, user_id = data.split("_")
            user_id = int(user_id)

            if user_id not in approved_users:
                await context.bot.send_message(chat_id=user_id, text="❌ لم يتم الموافقة على الدفع.")
                return

            # إرسال اختيار اللعبة بعد اختيار الجهاز
            game_selection_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🎮 The Challenge", callback_data=f"game_thechallenge_{device_code}_{user_id}"),
                    InlineKeyboardButton("🐔 Chicken Life", callback_data=f"game_chickenlife_{device_code}_{user_id}")
                ]
            ])

            await context.bot.send_message(
                chat_id=user_id,
                text="🎯 اختر اللعبة التي تريد تحميلها:",
                reply_markup=game_selection_keyboard
            )

        # اختيار اللعبة وطلب رابط تحميل مؤقت من السيرفر
        elif data.startswith("game_"):
            _, game_name, device_code, user_id = data.split("_")
            user_id = int(user_id)

            if user_id not in approved_users:
                await context.bot.send_message(chat_id=user_id, text="❌ لم يتم الموافقة على الدفع.")
                return

            # بيانات إرسالها للسيرفر
            payload = {
                "user_id": str(user_id),
                "device": device_code,
                "game": game_name.lower()
            }

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://gfdbgta.pythonanywhere.com/generate_link",
                        json=payload
                    ) as resp:
                        resp_data = await resp.json()
                        download_url = resp_data.get("download_url")
                        if download_url:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=(
                                    f"🔗 رابط تحميل لعبة "
                                    f"{game_name.replace('thechallenge', 'The Challenge').replace('chickenlife', 'Chicken Life')}:\n"
                                    f"{download_url}\n\n"
                                    "⚠️ صالح للتحميل لمرة واحدة فقط خلال 10 ثواني."
                                )
                            )
                            del approved_users[user_id]
                        else:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text="❌ فشل توليد رابط التحميل. حاول مرة أخرى لاحقًا."
                            )
            except Exception as e:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ فشل الاتصال بسيرفر التحميل."
                )
                print(f"❌ خطأ في توليد الرابط المؤقت: {e}")

    except Exception as e:
        print(f"❌ خطأ في button_handler: {e}")
        try:
            await query.edit_message_caption("❌ حدث خطأ أثناء معالجة الطلب.")
        except:
            pass

# تشغيل البوت
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

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))

    keep_alive()
    nest_asyncio.apply()

    print("🚀 بدء تشغيل البوت...")
    asyncio.run(main())
