import os
import asyncio
from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import aiohttp

# ========================
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "1077911771"))
SERVER_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:8080")

bot = Bot(TOKEN)
app = Flask(__name__)

# ========================
pending_payments = {}
approved_users = {}

# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 أهلاً بك في بوت تحميل الألعاب!\n\n"
        "⚠️ التحميل بعد الدفع:\n"
        "1️⃣ The Challenge\n"
        "2️⃣ Chicken Life\n\n"
        "💳 <b>طريقة الدفع:</b>\n"
        " تحويل المبلغ إلى بطاقة <b>ماستر كارد</b>:\n"
        "<code>7113282938</code>\n\n"
        "📩 بعد الدفع، أرسل صورة إيصال الدفع هنا.\n"
        "⚠️ الألعاب متاحة فقط على أجهزة الأندرويد حالياً.\n"
        "📞 للتواصل: <a href='https://www.instagram.com/ta_smg'>اضغط هنا</a>"
    )
    await update.message.reply_text(welcome, parse_mode="HTML")

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
        caption=f"مراجعة إيصال دفع من المستخدم: {user_id}",
        reply_markup=keyboard
    )
    await update.message.reply_text("📩 تم استلام الإيصال وسيتم مراجعته قريبًا.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("approve_"):
        user_id = int(data.split("_")[1])
        if user_id in pending_payments:
            approved_users[user_id] = True
            del pending_payments[user_id]

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📱 أندرويد", callback_data=f"device_android_{user_id}")
            ]])
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ تم قبول الدفع! اختر نوع جهازك:",
                reply_markup=keyboard
            )
            await query.edit_message_caption(f"✅ تم قبول الدفع للمستخدم: {user_id}")

    elif data.startswith("reject_"):
        user_id = int(data.split("_")[1])
        if user_id in pending_payments:
            del pending_payments[user_id]
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ تم رفض إيصال الدفع. يرجى التواصل معنا للتحقق."
            )
            await query.edit_message_caption(f"❌ تم رفض الدفع للمستخدم: {user_id}")

    elif data.startswith("device_"):
        _, device, user_id = data.split("_")
        user_id = int(user_id)
        if user_id not in approved_users:
            await context.bot.send_message(chat_id=user_id, text="❌ لم يتم الموافقة على الدفع.")
            return

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎮 The Challenge", callback_data=f"game_thechallenge_{user_id}_{device}"),
            InlineKeyboardButton("🐔 Chicken Life", callback_data=f"game_chickenlife_{user_id}_{device}")
        ]])
        await context.bot.send_message(chat_id=user_id, text="🎯 اختر اللعبة:", reply_markup=keyboard)

    elif data.startswith("game_"):
        _, game_name, user_id, device = data.split("_")
        user_id = int(user_id)
        if user_id not in approved_users:
            await context.bot.send_message(chat_id=user_id, text="❌ لم يتم الموافقة على الدفع.")
            return

        payload = {"user_id": str(user_id), "game": game_name}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{SERVER_URL}/generate_link", json=payload) as resp:
                    resp_data = await resp.json()
                    download_url = resp_data.get("download_url")
                    if download_url:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=f"🔗 رابط تحميل {game_name.replace('thechallenge','The Challenge').replace('chickenlife','Chicken Life')}:\n{download_url}\n⚠️ صالح لمرة واحدة خلال 10 ثواني."
                        )
                        del approved_users[user_id]
                    else:
                        await context.bot.send_message(chat_id=user_id, text="❌ فشل توليد رابط التحميل.")
        except Exception as e:
            await context.bot.send_message(chat_id=user_id, text="⚠️ فشل الاتصال بسيرفر التحميل.")
            print(f"❌ خطأ في طلب الرابط: {e}")

# ========================
# Webhook route
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    asyncio.run(handle_update(update))
    return "ok"

async def handle_update(update):
    application = await ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_handler))
    await application.process_update(update)

# ========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
