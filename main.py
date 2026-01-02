# =========================
# IMPORTS
# =========================
import os
import sys
import time
import hmac
import hashlib
import sqlite3
import asyncio
import aiohttp
import nest_asyncio
from threading import Thread
from flask import Flask, request, jsonify, redirect, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# =========================
# CONFIGURATION
# =========================
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"  # توكن البوت
ADMIN_CHAT_ID = 1077911771  # معرف الأدمن
SERVER_HOST = "https://gfdbgta.pythonanywhere.com"  # رابط السيرفر عند النشر
SECRET_KEY = b"ta_smg#F9!KX7@R2$wZ%M8^"  # مفتاح HMAC آمن
ADMIN_PASSWORD = "ta_smg!Z9@2026#"  # كلمة سر لوحة الأدمن

DOWNLOAD_LINKS = {
    "thechallenge": "https://www.dropbox.com/scl/fi/3erw8rjjv3gcx01op7iu0/The-Challenge.apk?dl=1",
    "chickenlife": "https://www.dropbox.com/scl/fi/0v4lovtvvlxsuezu3jerh/Chicken-Life.apk?dl=1"
}

# =========================
# DATABASE SETUP
# =========================
db = sqlite3.connect("db.sqlite", check_same_thread=False)
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    game TEXT,
    device_id TEXT,
    downloads INTEGER DEFAULT 0
)
""")
db.commit()

# =========================
# HELPER FUNCTIONS
# =========================
def sign(user_id, game):
    """توليد توقيع HMAC لكل مستخدم"""
    return hmac.new(SECRET_KEY, f"{user_id}:{game}".encode(), hashlib.sha256).hexdigest()

def verify(user_id, game, sig):
    """التحقق من صحة التوقيع"""
    return hmac.compare_digest(sign(user_id, game), sig)

# =========================
# FLASK SERVER
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Server is running."

@app.route("/authorize", methods=["POST"])
def authorize():
    data = request.json
    user_id = str(data["user_id"])
    game = data["game"]
    sig = data["signature"]

    if not verify(user_id, game, sig):
        return jsonify({"error": "unauthorized"}), 403

    # سجل المستخدم إذا لم يكن موجودًا
    cur = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        db.execute("INSERT INTO users(user_id, game) VALUES (?,?)", (user_id, game))
        db.commit()

    return jsonify({"url": f"/download/{user_id}"})


@app.route("/download/<user_id>")
def download(user_id):
    # ربط التحميل بالجهاز
    device_id = request.cookies.get("device_id") or str(request.remote_addr)
    cur = db.execute("SELECT game, device_id FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()

    if not row:
        return "❌ لم يتم السماح لك بالتحميل."

    game, saved_device = row
    if saved_device and saved_device != device_id:
        return "🚫 هذا الجهاز غير مصرح له بالتحميل."

    if not saved_device:
        db.execute("UPDATE users SET device_id=?, downloads=downloads+1 WHERE user_id=?", (device_id, user_id))
        db.commit()

    return redirect(DOWNLOAD_LINKS[game])

# =========================
# ADMIN PANEL
# =========================
@app.route("/admin")
def admin_panel():
    if request.args.get("pass") != ADMIN_PASSWORD:
        return "<h3 style='color:red;'>❌ تم رفض الدخول</h3>"

    users = db.execute("SELECT * FROM users").fetchall()

    html = """
    <html>
    <head>
    <title>لوحة تحكم الأدمن</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f6f8; color: #333; padding: 20px; }
        h1 { color: #2c3e50; }
        table { border-collapse: collapse; width: 100%; background: #fff; }
        th, td { padding: 12px 15px; text-align: center; }
        th { background-color: #3498db; color: white; }
        tr:nth-child(even) { background-color: #f2f2f2; }
        tr:hover { background-color: #d1ecf1; }
        .downloads { color: green; font-weight: bold; }
        .device-missing { color: orange; font-weight: bold; }
    </style>
    </head>
    <body>
    <h1>🛠️ لوحة تحكم الأدمن</h1>
    <p>عرض جميع المستخدمين، الألعاب، الأجهزة وعدد مرات التحميل.</p>
    <table>
    <tr><th>User ID</th><th>Game</th><th>Device</th><th>Downloads</th></tr>
    """
    for u in users:
        user_id, game, device, downloads = u
        device_class = "device-missing" if not device else ""
        html += f"<tr><td>{user_id}</td><td>{game}</td><td class='{device_class}'>{device or 'غير مرتبط'}</td><td class='downloads'>{downloads}</td></tr>"
    html += "</table></body></html>"
    return html

def run_flask():
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# =========================
# TELEGRAM BOT
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعليمات المستخدم واضحة عند بدء البوت"""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 The Challenge", callback_data="thechallenge")],
        [InlineKeyboardButton("🐔 Chicken Life", callback_data="chickenlife")]
    ])
    await update.message.reply_text(
        "👋 أهلاً بك في بوت تحميل الألعاب!\n\n"
        "⚠️ حالياً، الألعاب متوفرة فقط على أجهزة *الأندرويد*.\n"
        "⚠️ التحميل مرتبط بجهازك فقط.\n"
        "💳 بعد الدفع، اختر اللعبة لتحصل على الرابط.\n"
        "📱 الرابط لن يعمل على جهاز آخر.\n\n"
        "⬇ اختر اللعبة التي تريد تحميلها:",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار اللعبة وتحصل على الرابط مباشرة"""
    query = update.callback_query
    await query.answer()

    game = query.data
    user_id = str(query.from_user.id)

    # سجل المستخدم في قاعدة البيانات إذا لم يكن موجودًا
    cur = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        db.execute("INSERT INTO users(user_id, game) VALUES (?, ?)", (user_id, game))
        db.commit()

    payload = {"user_id": user_id, "game": game, "signature": sign(user_id, game)}

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{SERVER_HOST}/authorize", json=payload) as r:
            data = await r.json()

    await query.message.reply_text(
        f"⬇ رابط تحميل لعبتك:\n{SERVER_HOST}{data['url']}\n\n⚠️ الرابط مرتبط بجهازك فقط."
    )

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, lambda s,f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s,f: sys.exit(0))

    keep_alive()
    nest_asyncio.apply()

    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CallbackQueryHandler(choose_game))

    print("🤖 البوت يعمل الآن...")
    asyncio.run(app_bot.run_polling(drop_pending_updates=True))
