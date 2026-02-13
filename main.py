import os
import sys
import signal
import asyncio
import logging
import sqlite3
import aiohttp
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# ==========================
# CONFIGURATION
# ==========================
TOKEN = os.getenv("7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk")  # احفظ التوكن في Environment Variable
ADMIN_ID = 1077911771
SERVER_URL = "https://gfdbgta.pythonanywhere.com/generate_link"
APPROVAL_DURATION_MINUTES = 10

# قائمة الألعاب
GAMES = {
    "thechallenge": "🎮 The Challenge",
    "chickenlife": "🐔 Chicken Life"
}

# ==========================
# LOGGING
# ==========================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ==========================
# DATABASE SETUP
# ==========================
db = sqlite3.connect("bot.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    status TEXT,
    approved_until TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    game TEXT,
    date TEXT
)
""")
db.commit()

# ==========================
# DATABASE FUNCTIONS
# ==========================
def update_user(user_id, username=None, status=None, approved_until=None):
    cursor.execute("""
    INSERT INTO users (user_id, username, status, approved_until)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        username=COALESCE(excluded.username, users.username),
        status=COALESCE(excluded.status, users.status),
        approved_until=COALESCE(excluded.approved_until, users.approved_until)
    """, (user_id, username, status, approved_until))
    db.commit()

def get_user(user_id):
    cursor.execute("SELECT status, approved_until FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()

def is_approved(user_id):
    data = get_user(user_id)
    if not data:
        return False
    status, expiry = data
    if status != "approved":
        return False
    if expiry and datetime.utcnow() > datetime.fromisoformat(expiry):
        update_user(user_id, status="expired", approved_until=None)
        return False
    return True

def log_download(user_id, game):
    cursor.execute(
        "INSERT INTO downloads (user_id, game, date) VALUES (?, ?, ?)",
        (user_id, game, datetime.utcnow().isoformat())
    )
    db.commit()

# ==========================
# START COMMAND
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    update_user(user.id, user.username, "new")

    await update.message.reply_text(
        "مرحباً بكم في البوت الرسمي لتحميل الألعاب 🎮\n\n"
        "آلية الاستخدام:\n"
        "1️⃣ تحويل مبلغ الشراء إلى رقم الدفع الموضح أدناه.\n"
        "2️⃣ إرسال صورة واضحة لإيصال التحويل داخل هذا البوت.\n"
        "3️⃣ انتظار مراجعة الطلب من قبل الإدارة.\n"
        "4️⃣ بعد الموافقة، سيتم تفعيل خيار اختيار اللعبة واستلام رابط التحميل.\n\n"
        "رقم الدفع:\n"
        "<code>7113282938</code>\n\n"
        "تنبيه هام:\n"
        "• رابط التحميل مؤقت فقط وصالح لفترة محدودة.\n"
        "• صلاحية اختيار اللعبة تكون لمدة محددة بعد الموافقة.\n"
        "• يمنع مشاركة رابط التحميل مع أي طرف آخر.\n"
        "• في حال انتهاء الصلاحية، يتوجب إعادة إرسال إيصال الدفع لإكمال العملية.\n\n"
        "نشكر ثقتكم ودعمكم.",
        parse_mode="HTML"
    )

# ==========================
# HANDLE PAYMENT PHOTO
# ==========================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    update_user(user.id, user.username, "pending")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve:{user.id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject:{user.id}")
        ]
    ])

    await context.bot.send_photo(
        ADMIN_ID,
        update.message.photo[-1].file_id,
        caption=f"🧾 إيصال جديد\nID: {user.id}\n@{user.username}",
        reply_markup=keyboard
    )

    await update.message.reply_text(
        "تم استلام إيصال الدفع بنجاح ✅\n"
        "سيتم مراجعة الطلب من قبل الإدارة خلال وقت قصير."
    )

# ==========================
# CALLBACK BUTTONS
# ==========================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    action = parts[0]

    # إدارة الإيصال
    if action in ["approve", "reject"]:
        if query.from_user.id != ADMIN_ID:
            await query.answer("غير مصرح", show_alert=True)
            return

        user_id = int(parts[1])

        if action == "approve":
            expiry = datetime.utcnow() + timedelta(minutes=APPROVAL_DURATION_MINUTES)
            update_user(user_id, status="approved", approved_until=expiry.isoformat())

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(GAMES["thechallenge"], callback_data=f"game:thechallenge:{user_id}"),
                    InlineKeyboardButton(GAMES["chickenlife"], callback_data=f"game:chickenlife:{user_id}")
                ]
            ])

            await context.bot.send_message(
                user_id,
                "تمت الموافقة على عملية الدفع ✅\n\n"
                "يمكنك الآن اختيار اللعبة من الأزرار أدناه.\n"
                f"تنبيه: لديك {APPROVAL_DURATION_MINUTES} دقائق لاختيار اللعبة قبل انتهاء الصلاحية.",
                reply_markup=keyboard
            )

            await query.edit_message_caption("✅ تم القبول")

        else:
            update_user(user_id, status="rejected")
            await context.bot.send_message(user_id, "تم رفض الإيصال ❌\n\nيرجى إعادة الإرسال إذا كان هناك خطأ.")
            await query.edit_message_caption("🚫 مرفوض")

    # اختيار اللعبة
    elif action == "game":
        game = parts[1]
        user_id = int(parts[2])

        if query.from_user.id != user_id:
            await query.answer("غير مصرح", show_alert=True)
            return

        if not is_approved(user_id):
            await context.bot.send_message(user_id, "انتهت صلاحية الموافقة ⏰\n\nيرجى إعادة إرسال إيصال الدفع.")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(SERVER_URL, json={"game": game}) as resp:
                    result = await resp.json()
                    link = result.get("download_url")

                    if link:
                        log_download(user_id, game)
                        update_user(user_id, status="completed")

                        await context.bot.send_message(
                            user_id,
                            f"رابط التحميل الخاص بك:\n{link}\n\n"
                            "تنبيه:\n"
                            "• الرابط مؤقت فقط.\n"
                            "• يمنع مشاركة الرابط مع الآخرين.\n"
                            "• في حال مواجهة أي مشكلة يرجى التواصل مع الإدارة."
                        )
                    else:
                        await context.bot.send_message(user_id, "فشل إنشاء رابط التحميل ❌")

        except Exception as e:
            logging.error(str(e))
            await context.bot.send_message(user_id, "حدث خطأ في الاتصال بالسيرفر ⚠️")

# ==========================
# ADMIN STATS
# ==========================
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return

    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM downloads")
    downloads = cursor.fetchone()[0]

    await update.message.reply_text(
        f"لوحة الإحصائيات الإدارية 📊\n\n"
        f"إجمالي المستخدمين المسجلين: {users}\n"
        f"إجمالي التحميلات: {downloads}"
    )

# ==========================
# RUN BOT
# ==========================
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(buttons))

    logging.info("Bot is running...")
    await app.run_polling()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    asyncio.run(main())
