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
# 🚀 تشغيل سيرفر Flask لتبقي البوت حي على منصات مثل Replit أو Render
app = Flask('')

@app.route('/')
def home():
    return "✅ Bot is alive and running!"

def run():
    # تشغيل السيرفر على جميع الواجهات (IP) والمنفذ 8080
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

def keep_alive():
    # تشغيل السيرفر في ثريد مستقل كي لا يوقف البوت
    t = Thread(target=run)
    t.daemon = True
    t.start()

# ========================
# ⚙️ إعدادات البوت

# توكن البوت الخاص بك (خذ من BotFather)
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"

# معرف حسابك الخاص في تيليجرام (لإرسال الإيصالات والموافقة عليها)
ADMIN_CHAT_ID = 1077911771

# تخزين المستخدمين الذين أرسلوا إيصالات (في انتظار مراجعة)
pending_payments = {}

# تخزين المستخدمين الذين تم الموافقة عليهم للدفع
approved_users = {}

# ========================
# ✉️ دالة الرد على أمر /start
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
    # إرسال رسالة الترحيب مع تعليمات الدفع
    await update.message.reply_text(welcome, parse_mode="HTML")

# ========================
# 📸 دالة استقبال صورة إيصال الدفع
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    file_id = update.message.photo[-1].file_id  # أخذ أكبر صورة (الأعلى جودة)

    # حفظ حالة انتظار الموافقة للمستخدم
    pending_payments[user_id] = file_id

    # إعداد أزرار القبول والرفض للمسؤول (الأدمن)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")
        ]
    ])

    # إرسال صورة الإيصال مع الأزرار إلى حساب الأدمن لمراجعتها
    await context.bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=file_id,
        caption=f"مراجعة إيصال دفع من المستخدم: {user_id}",
        reply_markup=keyboard
    )

    # إبلاغ المستخدم بأن الإيصال استلم وسيتم مراجعته
    await update.message.reply_text("📩 تم استلام الإيصال وسيتم مراجعته قريبًا.")

# ========================
# 🎛️ دالة التعامل مع أزرار القبول والرفض واختيار الجهاز
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        data = query.data

        # ----------- قبول الدفع -----------
        if data.startswith("approve_"):
            user_id = int(data.split("_")[1])

            # تحقق وجود الإيصال في الانتظار
            if user_id in pending_payments:
                # جلب بيانات المستخدم لعرض الاسم
                user = await context.bot.get_chat(user_id)
                username = user.full_name
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # حفظ حالة الموافقة مع الوقت وحالة
                approved_users[user_id] = {
                    'approved_time': time.time(),
                    'status': 'approved'
                }
                # حذف المستخدم من الانتظار
                del pending_payments[user_id]

                # إرسال أزرار اختيار نوع الجهاز للمستخدمkeyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📱 أندرويد", callback_data=f"device_android_{user_id}"),
                        InlineKeyboardButton("🍎 آيفون", callback_data=f"device_ios_{user_id}")
                    ]
                ])

                # إبلاغ المستخدم بقبول الدفع وطلب اختيار الجهاز
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ تم قبول الدفع بنجاح!\n\n📲 يرجى اختيار نوع جهازك لتحصل على رابط التحميل:",
                    reply_markup=keyboard
                )

                # تعديل رسالة الأدمن بتفاصيل الدفع والانتظار لاختيار الجهاز
                await query.edit_message_caption(
                    f"✅ تم قبول الدفع.\n"
                    f"👤 الاسم: {username}\n"
                    f"🆔 المعرف: {user_id}\n"
                    f"⏰ الوقت: {now_str}\n"
                    "المستخدم في انتظار اختيار نوع الجهاز."
                )
            else:
                await query.edit_message_caption("⚠️ لم يتم العثور على إيصال لهذا المستخدم.")

        # ----------- رفض الدفع -----------
        elif data.startswith("reject_"):
            user_id = int(data.split("_")[1])
            if user_id in pending_payments:
                user = await context.bot.get_chat(user_id)
                username = user.full_name
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # حذف الإيصال من الانتظار
                del pending_payments[user_id]

                # إرسال رسالة رفض الدفع للمستخدم مع رابط التواصل
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ تم رفض إيصال الدفع.\n\n🔍 يرجى التحقق من المعلومات أو التواصل معنا:\n📱 https://www.instagram.com/ta_smg"
                )

                # تحديث رسالة الأدمن لتوثيق الرفض
                await query.edit_message_caption(
                    f"🚫 تم رفض الدفع.\n"
                    f"👤 الاسم: {username}\n"
                    f"🆔 المعرف: {user_id}\n"
                    f"⏰ الوقت: {now_str}"
                )
            else:
                await query.edit_message_caption("⚠️ لم يتم العثور على إيصال لهذا المستخدم.")

        # ----------- اختيار نوع الجهاز -----------
        elif data.startswith("device_"):
            _, device_code, user_id = data.split("_")
            user_id = int(user_id)

            # التأكد من أن المستخدم معتمد
            if user_id not in approved_users:
                await context.bot.send_message(chat_id=user_id, text="❌ لم يتم الموافقة على الدفع.")
                return

            # طلب رابط تحميل مؤقت من سيرفر Flask خارجي
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://gfdbgta.pythonanywhere.com/generate_link",
                        json={"user_id": str(user_id), "device": device_code}
                    ) as resp:
                        data = await resp.json()

                        # إذا تم توليد الرابط بنجاح
                        if "download_url" in data:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=f"🔗 رابط التحميل:\n{data['download_url']}\n\n⚠️ صالح للتحميل لمرة واحدة فقط خلال 10 ثواني."
                            )
                            # حذف المستخدم من قائمة المعتمدين بعد إرسال الرابط
                            del approved_users[user_id]
                        else:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text="❌ فشل توليد رابط التحميل. حاول مرة أخرى لاحقًا."
                            )
            except Exception as e:
                await context.bot.send_message(chat_id=user_id, text="⚠️ فشل الاتصال بسيرفر التحميل.")
                print(f"❌ خطأ في توليد الرابط المؤقت: {e}")

    except Exception as e:print(f"❌ خطأ في button_handler: {e}")
        try:
            await query.edit_message_caption("❌ حدث خطأ أثناء معالجة الطلب.")
        except:
            pass

# ========================
# 🔄 تشغيل البوت
async def main():
    try:
        application = ApplicationBuilder().token(TOKEN).build()

        # إضافة أوامر ومراقبين (handlers)
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        application.add_handler(CallbackQueryHandler(button_handler))

        print("🤖 البوت يعمل الآن...")
        await application.run_polling(drop_pending_updates=True)
    except Exception as e:
        print(f"❌ خطأ في تشغيل البوت: {e}")

# ========================
# ✅ نقطة الدخول لتشغيل البوت والسيرفر Flask
if name == "__main__":
    try:
        # التعامل مع الإشارات لإنهاء نظيف عند Ctrl+C أو SIGTERM
        signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))

        # تشغيل سيرفر Flask في ثريد مستقل
        keep_alive()

        # تطبيق nest_asyncio للسماح بالتشغيل المتداخل asyncio (مفيد لـ Replit وغيرها)
        nest_asyncio.apply()

        print("🚀 بدء تشغيل البوت...")
        asyncio.run(main())

    except KeyboardInterrupt:
        print("🛑 تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        print(f"❌ خطأ عام: {e}")
