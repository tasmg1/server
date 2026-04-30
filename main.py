import os
import asyncio
import aiohttp
import sqlite3

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# ---------------- CONFIG ----------------
TOKEN = os.getenv("8721383387:AAHeQ9Z1s3mIF6O6IdJFGR1DQ61bXS7hoU0")
ADMIN_ID = int(os.getenv("8569699093"))

# ---------------- DATABASE ----------------
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
cursor.execute("CREATE TABLE IF NOT EXISTS downloads (user_id INTEGER, game TEXT)")
conn.commit()

# ---------------- MEMORY ----------------
pending_payments = {}
approved_users = {}

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id

    cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (uid,))
    conn.commit()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 الألعاب", callback_data="games")],
        [InlineKeyboardButton("💳 الدفع", callback_data="pay")]
    ])

    await update.message.reply_text(
        "🎮 Play Zone\n\nاختر خيار:",
        reply_markup=keyboard
    )

# ---------------- PAYMENT INFO ----------------
async def show_payment(update, context):
    await context.bot.send_message(
        chat_id=update.from_user.id,
        text="💳 الدفع إلى:\n7113282938\n\n📩 بعد الدفع أرسل الإيصال"
    )

# ---------------- GAMES ----------------
def games_keyboard(uid):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("The Challenge", callback_data=f"game_thechallenge_{uid}"),
            InlineKeyboardButton("Chicken Life", callback_data=f"game_chickenlife_{uid}")
        ]
    ])

# ---------------- PHOTO (RECEIPT) ----------------
async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    file_id = update.message.photo[-1].file_id

    pending_payments[uid] = file_id

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("قبول", callback_data=f"ok_{uid}"),
            InlineKeyboardButton("رفض", callback_data=f"no_{uid}")
        ]
    ])

    await context.bot.send_photo(
        ADMIN_ID,
        photo=file_id,
        caption=f"طلب دفع من: {uid}",
        reply_markup=kb
    )

    await update.message.reply_text("تم استلام الإيصال")

# ---------------- BUTTONS ----------------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # الألعاب
    if data == "games":
        await context.bot.send_message(
            q.from_user.id,
            "🎮 اختر لعبة:",
            reply_markup=games_keyboard(q.from_user.id)
        )

    # الدفع
    elif data == "pay":
        await show_payment(q, context)

    # قبول الدفع
    elif data.startswith("ok_"):
        uid = int(data.split("_")[1])
        approved_users[uid] = True
        pending_payments.pop(uid, None)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Android", callback_data=f"dev_android_{uid}"),
                InlineKeyboardButton("iOS", callback_data=f"dev_ios_{uid}")
            ]
        ])

        await context.bot.send_message(uid, "تم التحقق من الدفع\nاختر الجهاز:", reply_markup=kb)

    # رفض الدفع
    elif data.startswith("no_"):
        uid = int(data.split("_")[1])
        pending_payments.pop(uid, None)
        await context.bot.send_message(uid, "لم يتم قبول الدفع")

    # الجهاز
    elif data.startswith("dev_"):
        _, device, uid = data.split("_")
        uid = int(uid)

        if uid not in approved_users:
            return

        await context.bot.send_message(
            uid,
            "🎮 اختر اللعبة:",
            reply_markup=games_keyboard(uid)
        )

    # اللعبة
    elif data.startswith("game_"):
        _, game, uid = data.split("_")
        uid = int(uid)

        if uid not in approved_users:
            await context.bot.send_message(uid, "الدفع غير مكتمل")
            return

        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://gfdbgta.pythonanywhere.com/generate_link",
                json={
                    "user_id": str(uid),
                    "game": game
                }
            ) as r:
                data = await r.json()
                url = data.get("download_url")

                if url:
                    cursor.execute("INSERT INTO downloads VALUES (?,?)", (uid, game))
                    conn.commit()

                    await context.bot.send_message(
                        uid,
                        f"🔗 رابط التحميل:\n{url}"
                    )

                    approved_users.pop(uid, None)

# ---------------- MAIN ----------------
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, photo))
    app.add_handler(CallbackQueryHandler(buttons))

    print("Play Zone Running...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
