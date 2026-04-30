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
cursor.execute("CREATE TABLE IF NOT EXISTS games (name TEXT, code TEXT)")
cursor.execute("CREATE TABLE IF NOT EXISTS downloads (user_id INTEGER, game_code TEXT)")
conn.commit()

# ألعابك
cursor.execute("INSERT OR IGNORE INTO games VALUES ('The Challenge','thechallenge')")
cursor.execute("INSERT OR IGNORE INTO games VALUES ('Chicken Life','chickenlife')")
conn.commit()

# ---------------- MEMORY ----------------
pending = {}
approved = {}

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id

    cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (uid,))
    conn.commit()

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("الألعاب", callback_data="games")],
        [InlineKeyboardButton("الدفع", callback_data="pay")]
    ])

    await update.message.reply_text("Play Zone\nاختر خيار:", reply_markup=kb)

# ---------------- SHOW GAMES ----------------
async def show_games(uid, context, device="android"):
    cursor.execute("SELECT name, code FROM games")
    games = cursor.fetchall()

    buttons = [[InlineKeyboardButton(g[0], callback_data=f"game_{g[1]}_{device}_{uid}")]
               for g in games]

    await context.bot.send_message(
        chat_id=uid,
        text="قائمة الألعاب",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ---------------- PHOTO ----------------
async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    pending[uid] = True

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("قبول", callback_data=f"ok_{uid}"),
            InlineKeyboardButton("رفض", callback_data=f"no_{uid}")
        ]
    ])

    await context.bot.send_message(ADMIN_ID, f"دفع جديد من {uid}", reply_markup=kb)
    await update.message.reply_text("تم استلام الإيصال")

# ---------------- BUTTONS ----------------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "games":
        await show_games(q.from_user.id, context)

    elif data == "pay":
        await context.bot.send_message(q.from_user.id, "الدفع إلى: 7113282938")

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

        await context.bot.send_message(uid, "تم التحقق", reply_markup=kb)

    elif data.startswith("no_"):
        uid = int(data.split("_")[1])
        pending.pop(uid, None)
        await context.bot.send_message(uid, "لم يتم التحقق")

    elif data.startswith("dev_"):
        _, device, uid = data.split("_")
        uid = int(uid)

        if uid not in approved:
            return

        await show_games(uid, context, device)

    elif data.startswith("game_"):
        _, game, device, uid = data.split("_")
        uid = int(uid)

        if uid not in approved:
            await context.bot.send_message(uid, "الدفع غير مكتمل")
            return

        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://gfdbgta.pythonanywhere.com/generate_link",
                json={"user_id": str(uid), "device": device, "game": game}
            ) as r:
                data = await r.json()
                url = data.get("download_url")

                if url:
                    cursor.execute("INSERT INTO downloads VALUES (?,?)", (uid, game))
                    conn.commit()

                    await context.bot.send_message(uid, f"رابط التحميل:\n{url}")

                    approved.pop(uid, None)

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
