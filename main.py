import os
import asyncio
import aiohttp
import nest_asyncio
import sqlite3

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

# ---------------- KEEP ALIVE ----------------
app = Flask("")

@app.route("/")
def home():
    return "OK"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("8721383387:AAHeQ9Z1s3mIF6O6IdJFGR1DQ61bXS7hoU0")
ADMIN_ID = int(os.getenv("8569699093"))

# ---------------- DATABASE ----------------
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
cursor.execute("CREATE TABLE IF NOT EXISTS games (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, code TEXT)")
cursor.execute("CREATE TABLE IF NOT EXISTS downloads (user_id INTEGER, game_code TEXT)")
conn.commit()

# ألعابك (مرة واحدة فقط)
cursor.execute("INSERT OR IGNORE INTO games (name, code) VALUES ('The Challenge','thechallenge')")
cursor.execute("INSERT OR IGNORE INTO games (name, code) VALUES ('Chicken Life','chickenlife')")
conn.commit()

# ---------------- MEMORY ----------------
pending = {}
approved = {}

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("الألعاب", callback_data="games")],
        [InlineKeyboardButton("الدفع", callback_data="pay")]
    ])

    await update.message.reply_text(
        "Play Zone\n\nمركز تحميل الألعاب\nاختر خيار:",
        reply_markup=keyboard
    )

# ---------------- SHOW GAMES ----------------
async def show_games(user_id, context, device="android"):
    cursor.execute("SELECT name, code FROM games")
    games = cursor.fetchall()

    buttons = [[InlineKeyboardButton(g[0], callback_data=f"game_{g[1]}_{device}_{user_id}")]
               for g in games]

    await context.bot.send_message(
        chat_id=user_id,
        text="قائمة الألعاب\nاختر لعبة:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ---------------- PHOTO (PAYMENT) ----------------
async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    file_id = update.message.photo[-1].file_id

    pending[user_id] = file_id

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("قبول", callback_data=f"ok_{user_id}"),
            InlineKeyboardButton("رفض", callback_data=f"no_{user_id}")
        ]
    ])

    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=file_id,
        caption=f"طلب دفع: {user_id}",
        reply_markup=keyboard
    )

    await update.message.reply_text("تم استلام الإيصال")

# ---------------- BUTTONS ----------------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # الألعاب
    if data == "games":
        await show_games(q.from_user.id, context)

    # الدفع
    elif data == "pay":
        await context.bot.send_message(
            chat_id=q.from_user.id,
            text="الدفع مطلوب\n7113282938\nأرسل الإيصال بعد الدفع"
        )

    # قبول
    elif data.startswith("ok_"):
        uid = int(data.split("_")[1])
        approved[uid] = True
        pending.pop(uid, None)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Android", callback_data=f"dev_android_{uid}"),
                InlineKeyboardButton("iOS", callback_data=f"dev_ios_{uid}")
            ]
        ])

        await context.bot.send_message(
            chat_id=uid,
            text="تم التحقق\nاختر الجهاز:",
            reply_markup=kb
        )

    # رفض
    elif data.startswith("no_"):
        uid = int(data.split("_")[1])
        pending.pop(uid, None)

        await context.bot.send_message(chat_id=uid, text="لم يتم التحقق من الدفع")

    # الجهاز
    elif data.startswith("dev_"):
        _, device, uid = data.split("_")
        uid = int(uid)

        if uid not in approved:
            return

        await show_games(uid, context, device)

    # اللعبة
    elif data.startswith("game_"):
        _, game, device, uid = data.split("_")
        uid = int(uid)

        if uid not in approved:
            await context.bot.send_message(chat_id=uid, text="الدفع غير مكتمل")
            return

        payload = {
            "user_id": str(uid),
            "device": device,
            "game": game
        }

        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://gfdbgta.pythonanywhere.com/generate_link",
                json=payload
            ) as r:
                res = await r.json()
                url = res.get("download_url")

                if url:
                    cursor.execute(
                        "INSERT INTO downloads VALUES (?,?)",
                        (uid, game)
                    )
                    conn.commit()

                    await context.bot.send_message(
                        chat_id=uid,
                        text=f"رابط التحميل:\n{url}\n\nالرابط مؤقت"
                    )

                    approved.pop(uid, None)

# ---------------- STATS ----------------
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT game_code, COUNT(*) FROM downloads GROUP BY game_code")
    data = cursor.fetchall()

    msg = "الإحصائيات\n\n"
    for d in data:
        msg += f"{d[0]} : {d[1]}\n"

    await update.message.reply_text(msg)

# ---------------- MAIN ----------------
async def main():
    bot = ApplicationBuilder().token(TOKEN).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("stats", stats))
    bot.add_handler(MessageHandler(filters.PHOTO, photo))
    bot.add_handler(CallbackQueryHandler(buttons))

    print("Running Play Zone...")
    await bot.run_polling()

if __name__ == "__main__":
    keep_alive()
    nest_asyncio.apply()
    asyncio.run(main())
