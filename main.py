import asyncio
import nest_asyncio
from threading import Thread
from flask import Flask
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# ------------------------
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

# ------------------------
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"
ADMIN_CHAT_ID = 1077911771

pending_payments = {}
approved_users = {}

# ------------------------
async def start(update: Update, context):
    welcome = (
        "👋 أهلاً بك!\n\n"
        "💳 بعد الدفع، أرسل إيصال الدفع هنا.\n"
        "🎮 الألعاب: The Challenge و Chicken Life\n"
        "📞 دعم: @ta_smg"
    )
    await update.message.reply_text(welcome)

async def handle_photo(update: Update, context):
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

async def button_handler(update: Update, context):
    query = update.callback_query
    data = query.data
    await query.answer()

    try:
        if data.startswith("approve_"):
            user_id = int(data.split("_")[1])
            if user_id in pending_payments:
                approved_users[user_id] = True
                del pending_payments[user_id]

                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📱 أندرويد", callback_data=f"device_android_{user_id}"),
                    InlineKeyboardButton("🍎 آيفون", callback_data=f"device_ios_{user_id}")
                ]])

                await context.bot.send_message(chat_id=user_id, text="✅ تم قبول الدفع. اختر جهازك:", reply_markup=keyboard)
                await query.edit_message_text(f"✅ تم قبول دفع المستخدم {user_id}")

        elif data.startswith("reject_"):
            user_id = int(data.split("_")[1])
            if user_id in pending_payments:
                del pending_payments[user_id]
                await context.bot.send_message(chat_id=user_id, text="❌ تم رفض إيصال الدفع. حاول مرة أخرى.")
                await query.edit_message_text(f"🚫 تم رفض دفع المستخدم {user_id}")

        elif data.startswith("device_"):
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

        elif data.startswith("game_"):
            _, game, device, user_id = data.split("_")
            user_id = int(user_id)
            if user_id not in approved_users:
                await context.bot.send_message(chat_id=user_id, text="❌ لم يتم الموافقة على الدفع.")
                return

            payload = {"user_id": str(user_id), "device": device, "game": game.lower()}
            async with aiohttp.ClientSession() as session:
                async with session.post("https://gfdbgta.pythonanywhere.com/generate_link", json=payload) as resp:
                    data = await resp.json()
                    download_url = data.get("download_url")
                    if download_url:
                        await context.bot.send_message(chat_id=user_id, text=f"🎮 رابط تحميل {game}:\n{download_url}\n⚠️ صالح لمرة واحدة لمدة 10 ثوانٍ.")
                        del approved_users[user_id]
                    else:
                        await context.bot.send_message(chat_id=user_id, text="⚠️ حدث خطأ في توليد الرابط.")
    except Exception as e:
        print(f"❌ خطأ في button_handler: {e}")

# ------------------------
async def main():
    app_builder = ApplicationBuilder().token(TOKEN).build()
    app_builder.add_handler(CommandHandler("start", start))
    app_builder.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app_builder.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 البوت يعمل الآن...")
    await app_builder.run_polling(drop_pending_updates=True)

# ------------------------
if __name__ == "__main__":
    import sys, signal
    import nest_asyncio

    nest_asyncio.apply()
    keep_alive()

    try:
        signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))
        print("🚀 بدء تشغيل البوت...")
        asyncio.run(main())
    except Exception as e:
        print(f"❌ خطأ عام: {e}")
