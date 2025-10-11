import sys
import signal
import asyncio
import nest_asyncio
from threading import Thread
from flask import Flask
import aiohttp
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
# سيرفر مصغّر للحفاظ على البوت حي
app = Flask('')

@app.route('/')
def home():
    return "✅ Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8081, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# ========================
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"
ADMIN_CHAT_ID = 1077911771

pending_payments = {}
approved_users = {}

# ========================
# أوامر البوت
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 أهلاً بك في بوت تحميل الألعاب!\n\n"
        "⚠️ التحميل بعد الدفع:\n"
        "1️⃣ The Challenge\n"
        "2️⃣ Chicken Life\n\n"
        "💳 <b>طريقة الدفع:</b>\n"
        "تحويل المبلغ إلى بطاقة <b>ماستر كارد</b>:\n"
        "<code>7113282938</code>\n\n"
        "⚠️ أقل مبلغ للدفع هو 1000 دينار عراقي.\n\n"
        "📩 بعد الدفع، أرسل صورة إيصال الدفع هنا.\n"
        "🎮 الألعاب متاحة فقط على أجهزة الأندرويد حاليًا.\n"
        "📞 للدعم أو التواصل: <a href='https://www.instagram.com/ta_smg'>@ta_smg</a>"
    )
    await update.message.reply_text(welcome, parse_mode="HTML")

# استقبال صورة الإيصال
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    file_id = update.message.photo[-1].file_id
    pending_payments[user_id] = file_id

    keyboard = InlineKeyboardMarkup([[ 
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_{user_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")
    ]])

    await context.bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=file_id,
        caption=f"🧾 إيصال من المستخدم {user_id}",
        reply_markup=keyboard
    )
    await update.message.reply_text("📩 تم استلام الإيصال، بانتظار المراجعة.")

# التعامل مع الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    try:
        # ✅ القبول
        if data.startswith("approve_"):
            await query.answer()
            user_id = int(data.split("_")[1])

            if user_id in pending_payments:
                approved_users[user_id] = {'time': asyncio.get_event_loop().time(), 'status': 'approved'}
                del pending_payments[user_id]

                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📱 أندرويد", callback_data=f"device_android_{user_id}"),
                    InlineKeyboardButton("🍎 آيفون", callback_data=f"device_ios_{user_id}")
                ]])

                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="✅ تم قبول الدفع.\nاختر نوع جهازك:",
                        reply_markup=keyboard
                    )
                except Exception as e:
                    print(f"❌ لم يتم إرسال الرسالة للمستخدم: {e}")

                await query.edit_message_text(f"✅ تم قبول دفع المستخدم {user_id}")

        # ❌ الرفض
        elif data.startswith("reject_"):
            await query.answer()
            user_id = int(data.split("_")[1])

            if user_id in pending_payments:
                del pending_payments[user_id]

                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="❌ تم رفض إيصال الدفع.\nيرجى التحقق وإعادة المحاولة."
                    )
                except Exception as e:
                    print(f"❌ لم يتم إرسال رسالة الرفض: {e}")

                await query.edit_message_text(f"🚫 تم رفض دفع المستخدم {user_id}")

        # اختيار الجهاز
        elif data.startswith("device_"):
            await query.answer()
            _, device, user_id = data.split("_")
            user_id = int(user_id)

            if user_id not in approved_users:
                await context.bot.send_message(chat_id=user_id, text="⚠️ لم يتم قبول الدفع بعد.")
                return

            keyboard = InlineKeyboardMarkup([[ 
                InlineKeyboardButton("🎮 The Challenge", callback_data=f"game_thechallenge_{device}_{user_id}"),
                InlineKeyboardButton("🐔 Chicken Life", callback_data=f"game_chickenlife_{device}_{user_id}")
            ]])

            await context.bot.send_message(chat_id=user_id, text="🎯 اختر اللعبة:", reply_markup=keyboard)

        # اختيار اللعبة
        elif data.startswith("game_"):
            await query.answer()
            _, game, device, user_id = data.split("_")
            user_id = int(user_id)

            if user_id not in approved_users:
                await context.bot.send_message(chat_id=user_id, text="❌ لم يتم الموافقة على الدفع.")
                return

            payload = {
                "user_id": str(user_id),
                "device": device,
                "game": game.lower()
            }

            try:
                # ✅ ربط البوت بسيرفر PythonAnywhere
                async with aiohttp.ClientSession() as session:
                    async with session.post("https://gfdbgta.pythonanywhere.com/generate_link", json=payload) as resp:
                        data = await resp.json()
                        download_url = data.get("download_url")

                        if download_url:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=(
                                    f"🎮 رابط تحميل {game.replace('thechallenge', 'The Challenge').replace('chickenlife', 'Chicken Life')}:\n"
                                    f"{download_url}\n\n"
                                    "⚠️ الرابط صالح لمرة واحدة فقط ولمدة 10 ثوانٍ."
                                )
                            )
                            del approved_users[user_id]
                        else:
                            await context.bot.send_message(chat_id=user_id, text="⚠️ حدث خطأ في توليد الرابط.")
            except Exception as e:
                await context.bot.send_message(chat_id=user_id, text="🚫 فشل الاتصال بسيرفر التحميل.")
                print(f"❌ خطأ في توليد الرابط: {e}")

    except Exception as e:
        print(f"❌ خطأ في button_handler: {e}")

# ========================
# تشغيل البوت
async def main():
    app_builder = ApplicationBuilder().token(TOKEN).build()
    app_builder.add_handler(CommandHandler("start", start))
    app_builder.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app_builder.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 البوت يعمل الآن...")
    await app_builder.run_polling(drop_pending_updates=True)

# ========================
# التشغيل الرئيسي
if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))

        keep_alive()
        nest_asyncio.apply()

        print("🚀 بدء تشغيل البوت...")
        asyncio.run(main())

    except KeyboardInterrupt:
        print("🛑 تم إيقاف البوت يدويًا")
    except Exception as e:
        print(f"❌ خطأ عام: {e}")
