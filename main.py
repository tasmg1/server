import os
import sys
import json
import time
import hmac
import uuid
import html
import signal
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from threading import Thread
from typing import Any, Dict, Optional

import aiohttp
import nest_asyncio
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# =====================================================
# PlayZone Telegram Bot - Professional & Secure Version
# =====================================================
# Security notes:
# 1) لا تضع BOT_TOKEN داخل الكود أو GitHub.
# 2) ضع BOT_TOKEN و ADMIN_CHAT_ID في Environment Variables.
# 3) إذا كان التوكن القديم نُشر سابقًا، غيّره من BotFather عبر /revoke.
# 4) هذا البوت يحفظ الطلبات في ملف JSON بسيط. للإنتاج الكبير استخدم SQLite/Firebase.
# =====================================================

# ---------------------- Logging ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("playzone-bot")

# ---------------------- Config -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID", "8569699093").strip()
DOWNLOAD_API_URL = os.getenv("DOWNLOAD_API_URL", "https://gfdbgta.pythonanywhere.com/generate_link").strip()
DOWNLOAD_API_SECRET = os.getenv("DOWNLOAD_API_SECRET", "").strip()

SUPPORT_URL = os.getenv("SUPPORT_URL", "https://instagram.com/p1ay.zone").strip()
PAYMENT_CARD = os.getenv("PAYMENT_CARD", "7113282938").strip()
GAME_PRICE = os.getenv("GAME_PRICE", "1000 IQD").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DB_FILE = DATA_DIR / "playzone_bot_data.json"

PAYMENT_TIMEOUT_SECONDS = int(os.getenv("PAYMENT_TIMEOUT_SECONDS", str(60 * 60 * 24)))  # 24h
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", str(60 * 60 * 2)))   # 2h
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "3"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
except ValueError:
    raise RuntimeError("ADMIN_CHAT_ID must be an integer")

# ---------------------- Flask keep-alive -------------
app = Flask(__name__)


@app.route("/")
def home():
    return "✅ PlayZone Bot is alive and running!"


def run_web_server():
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


def keep_alive():
    thread = Thread(target=run_web_server, daemon=True)
    thread.start()


# ---------------------- Static data ------------------
GAMES = {
    "thechallenge": {
        "title": "The Challenge",
        "emoji": "🎮",
        "description": "مغامرة مليئة بالتحديات والألغاز الشيقة.",
        "available_devices": ["android", "ios"],
    },
    "chickenlife": {
        "title": "Chicken Life",
        "emoji": "🐔",
        "description": "محاكاة ممتعة لعالم الدجاج مع تحديات مرحة.",
        "available_devices": ["android", "ios"],
    },
}

DEVICES = {
    "android": "📱 Android",
    "ios": "🍎 iPhone",
}

# ---------------------- Database ---------------------
DEFAULT_DB = {
    "pending_payments": {},
    "sessions": {},
    "orders": {},
    "rate_limits": {},
    "stats": {
        "started_users": 0,
        "submitted_receipts": 0,
        "approved_orders": 0,
        "rejected_orders": 0,
        "generated_links": 0,
    },
}

_db_lock = asyncio.Lock()
db: Dict[str, Any] = DEFAULT_DB.copy()


def utc_now_ts() -> int:
    return int(time.time())


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def user_key(user_id: int) -> str:
    return str(user_id)


def escape_text(value: Any) -> str:
    return html.escape(str(value or ""))


def mask_user_id(user_id: int) -> str:
    text = str(user_id)
    if len(text) <= 4:
        return text
    return f"{text[:2]}***{text[-2:]}"


def load_db_sync() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        return json.loads(json.dumps(DEFAULT_DB))

    try:
        with DB_FILE.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except Exception as error:
        logger.error("Could not read DB file: %s", error)
        return json.loads(json.dumps(DEFAULT_DB))

    merged = json.loads(json.dumps(DEFAULT_DB))
    for key, value in loaded.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def save_db_sync() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = DB_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(db, file, ensure_ascii=False, indent=2)
    temp_file.replace(DB_FILE)


async def save_db() -> None:
    async with _db_lock:
        save_db_sync()


async def update_db(mutator):
    async with _db_lock:
        result = mutator(db)
        save_db_sync()
        return result


async def cleanup_expired_data() -> None:
    now = utc_now_ts()

    def mutate(data):
        expired_payments = [
            key for key, payment in data["pending_payments"].items()
            if now - int(payment.get("created_ts", now)) > PAYMENT_TIMEOUT_SECONDS
        ]
        for key in expired_payments:
            data["pending_payments"].pop(key, None)

        expired_sessions = [
            key for key, session in data["sessions"].items()
            if now - int(session.get("updated_ts", session.get("created_ts", now))) > SESSION_TIMEOUT_SECONDS
        ]
        for key in expired_sessions:
            data["sessions"].pop(key, None)

        old_rate_limits = [
            key for key, ts in data["rate_limits"].items()
            if now - int(ts) > RATE_LIMIT_SECONDS * 10
        ]
        for key in old_rate_limits:
            data["rate_limits"].pop(key, None)

        return len(expired_payments), len(expired_sessions)

    removed_payments, removed_sessions = await update_db(mutate)
    if removed_payments or removed_sessions:
        logger.info("Cleaned expired data: payments=%s sessions=%s", removed_payments, removed_sessions)


async def is_rate_limited(user_id: int, action: str) -> bool:
    now = utc_now_ts()
    key = f"{user_id}:{action}"

    def mutate(data):
        last = int(data["rate_limits"].get(key, 0))
        if now - last < RATE_LIMIT_SECONDS:
            return True
        data["rate_limits"][key] = now
        return False

    return await update_db(mutate)


# ---------------------- Keyboards --------------------
def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 الألعاب", callback_data="menu:games")],
        [InlineKeyboardButton("💳 طريقة الدفع", callback_data="menu:payment")],
        [InlineKeyboardButton("📞 الدعم", url=SUPPORT_URL)],
    ])


def games_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for game_id, game in GAMES.items():
        buttons.append([
            InlineKeyboardButton(
                f"{game['emoji']} {game['title']}",
                callback_data=f"game:{game_id}",
            )
        ])
    buttons.append([InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")])
    return InlineKeyboardMarkup(buttons)


def devices_keyboard(game_id: str) -> InlineKeyboardMarkup:
    buttons = []
    available = GAMES[game_id].get("available_devices", [])
    for device_code in available:
        buttons.append([
            InlineKeyboardButton(DEVICES[device_code], callback_data=f"device:{game_id}:{device_code}")
        ])
    buttons.append([InlineKeyboardButton("⬅️ اختيار لعبة أخرى", callback_data="menu:games")])
    return InlineKeyboardMarkup(buttons)


def admin_review_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ قبول وإرسال الرابط", callback_data=f"admin:approve:{order_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"admin:reject:{order_id}"),
        ],
        [InlineKeyboardButton("ℹ️ معلومات الطلب", callback_data=f"admin:info:{order_id}")],
    ])


def back_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]])


# ---------------------- Text builders ----------------
def payment_text(game_title: Optional[str] = None, device_title: Optional[str] = None) -> str:
    selected = ""
    if game_title and device_title:
        selected = (
            f"🎮 اللعبة: <b>{escape_text(game_title)}</b>
"
            f"📲 الجهاز: <b>{escape_text(device_title)}</b>
"
        )

    return (
        "💳 <b>إتمام الدفع</b>

"
        f"{selected}"
        f"💰 السعر: <b>{escape_text(GAME_PRICE)}</b>

"
        "حوّل المبلغ إلى بطاقة <b>ماستر كارد</b>:
"
        f"<code>{escape_text(PAYMENT_CARD)}</code>

"
        "📸 بعد التحويل، أرسل صورة إيصال الدفع هنا.
"
        "✅ بعد قبول الإيصال من الإدارة، سيصلك رابط تحميل مؤقت خاص بك.

"
        "⚠️ تأكد أن الإيصال واضح ويظهر مبلغ التحويل."
    )


def order_caption(order: Dict[str, Any]) -> str:
    game_id = order.get("game")
    device_code = order.get("device")
    game_title = GAMES.get(game_id, {}).get("title", game_id)
    device_title = DEVICES.get(device_code, device_code)
    username = order.get("username") or "لا يوجد"

    return (
        "📩 <b>مراجعة إيصال دفع جديد</b>\n\n"
        f"🧾 Order: <code>{escape_text(order.get('order_id'))}</code>\n"
        f"👤 الاسم: {escape_text(order.get('full_name'))}\n"
        f"🔗 username: {escape_text('@' + username if username != 'لا يوجد' else username)}\n"
        f"🆔 ID: <code>{escape_text(order.get('user_id'))}</code>\n"
        f"🎮 اللعبة: {escape_text(game_title)}\n"
        f"📲 الجهاز: {escape_text(device_title)}\n"
        f"⏰ الوقت: {escape_text(order.get('created_at_text'))}\n"
        f"📌 الحالة: {escape_text(order.get('status', 'pending'))}"
    )


# ---------------------- Download API -----------------
def sign_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not DOWNLOAD_API_SECRET:
        return payload

    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(DOWNLOAD_API_SECRET.encode("utf-8"), message, "sha256").hexdigest()
    payload["signature"] = signature
    return payload


async def generate_download_link(user_id: int, game_id: str, device_code: str, order_id: str) -> Optional[str]:
    payload = {
        "user_id": str(user_id),
        "device": device_code,
        "game": game_id.lower(),
        "order_id": order_id,
        "timestamp": utc_now_ts(),
    }
    payload = sign_payload(payload)

    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(DOWNLOAD_API_URL, json=payload) as response:
            if response.status >= 400:
                body = await response.text()
                logger.error("Download API error status=%s body=%s", response.status, body[:300])
                return None
            data = await response.json()
            return data.get("download_url")


# ---------------------- Handlers ---------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_expired_data()

    if not update.message:
        return

    user = update.message.from_user
    key = user_key(user.id)

    def mutate(data):
        data["stats"]["started_users"] = int(data["stats"].get("started_users", 0)) + 1
        data["sessions"][key] = {
            "user_id": user.id,
            "full_name": user.full_name,
            "username": user.username or "",
            "created_ts": utc_now_ts(),
            "updated_ts": utc_now_ts(),
        }
    await update_db(mutate)

    args = context.args
    if args:
        payload = args[0].strip().lower()
        if payload in GAMES:
            def set_game(data):
                session = data["sessions"].setdefault(key, {})
                session.update({"game": payload, "updated_ts": utc_now_ts()})
            await update_db(set_game)

            game = GAMES[payload]
            await update.message.reply_text(
                f"👋 أهلاً بك في <b>PlayZone</b>\n\n"
                f"تم اختيار لعبة:\n{game['emoji']} <b>{escape_text(game['title'])}</b>\n\n"
                "اختر نوع جهازك:",
                parse_mode="HTML",
                reply_markup=devices_keyboard(payload),
            )
            return

    await update.message.reply_text(
        "👋 أهلاً بك في <b>PlayZone</b>\n\n"
        "اختر اللعبة، ثم نوع الجهاز، ثم ادفع 1000 IQD وأرسل صورة الإيصال لتحصل على رابط التحميل.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if await is_rate_limited(update.message.from_user.id, "text"):
        return

    await update.message.reply_text(
        "استخدم الأزرار لاختيار اللعبة أو أرسل صورة إيصال الدفع بعد التحويل.",
        reply_markup=main_menu_keyboard(),
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.message.from_user
    key = user_key(user.id)

    if await is_rate_limited(user.id, "photo"):
        await update.message.reply_text("⏳ انتظر قليلًا قبل إرسال إيصال آخر.")
        return

    session = db.get("sessions", {}).get(key, {})
    game_id = session.get("game")
    device_code = session.get("device")

    if not game_id or not device_code:
        await update.message.reply_text(
            "⚠️ قبل إرسال الإيصال، اختر اللعبة ونوع الجهاز أولاً.",
            reply_markup=games_keyboard(),
        )
        return

    if game_id not in GAMES or device_code not in DEVICES:
        await update.message.reply_text("❌ اختيار اللعبة أو الجهاز غير صحيح. ابدأ من جديد.", reply_markup=games_keyboard())
        return

    existing_pending = [
        order for order in db.get("pending_payments", {}).values()
        if str(order.get("user_id")) == str(user.id) and order.get("status") == "pending"
    ]
    if existing_pending:
        await update.message.reply_text("⚠️ لديك إيصال قيد المراجعة بالفعل. انتظر رد الأدمن.")
        return

    file_id = update.message.photo[-1].file_id
    order_id = uuid.uuid4().hex[:12]

    order = {
        "order_id": order_id,
        "file_id": file_id,
        "game": game_id,
        "device": device_code,
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username or "",
        "status": "pending",
        "created_ts": utc_now_ts(),
        "created_at": iso_now(),
        "created_at_text": now_text(),
    }

    def mutate(data):
        data["pending_payments"][order_id] = order
        data["orders"][order_id] = order.copy()
        data["stats"]["submitted_receipts"] = int(data["stats"].get("submitted_receipts", 0)) + 1
    await update_db(mutate)

    try:
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=file_id,
            caption=order_caption(order),
            parse_mode="HTML",
            reply_markup=admin_review_keyboard(order_id),
        )
    except TelegramError as error:
        logger.error("Failed to send receipt to admin: %s", error)
        await update.message.reply_text("⚠️ حدث خطأ أثناء إرسال الإيصال للمراجعة. حاول لاحقًا.")
        return

    await update.message.reply_text(
        "✅ تم استلام الإيصال.\nسيتم مراجعته قريبًا، وبعد القبول سيصلك رابط التحميل المؤقت."
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data or ""
    user_id = query.from_user.id

    try:
        if data == "menu:home":
            await query.edit_message_text(
                "👋 أهلاً بك في <b>PlayZone</b>\n\nاختر من القائمة:",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            return

        if data == "menu:games":
            await query.edit_message_text(
                "🎮 اختر اللعبة التي تريد تحميلها:",
                reply_markup=games_keyboard(),
            )
            return

        if data == "menu:payment":
            await query.edit_message_text(
                payment_text(),
                parse_mode="HTML",
                reply_markup=back_home_keyboard(),
            )
            return

        if data.startswith("game:"):
            game_id = data.split(":", 1)[1]
            if game_id not in GAMES:
                await query.edit_message_text("❌ هذه اللعبة غير متوفرة حالياً.")
                return

            key = user_key(user_id)

            def mutate(data_obj):
                session = data_obj["sessions"].setdefault(key, {})
                session.update({
                    "user_id": user_id,
                    "full_name": query.from_user.full_name,
                    "username": query.from_user.username or "",
                    "game": game_id,
                    "updated_ts": utc_now_ts(),
                })
            await update_db(mutate)

            game = GAMES[game_id]
            await query.edit_message_text(
                f"{game['emoji']} <b>{escape_text(game['title'])}</b>\n"
                f"{escape_text(game['description'])}\n\n"
                "اختر نوع جهازك:",
                parse_mode="HTML",
                reply_markup=devices_keyboard(game_id),
            )
            return

        if data.startswith("device:"):
            _, game_id, device_code = data.split(":")
            if game_id not in GAMES or device_code not in DEVICES:
                await query.edit_message_text("❌ اختيار غير صحيح.")
                return

            key = user_key(user_id)

            def mutate(data_obj):
                session = data_obj["sessions"].setdefault(key, {})
                session.update({
                    "user_id": user_id,
                    "full_name": query.from_user.full_name,
                    "username": query.from_user.username or "",
                    "game": game_id,
                    "device": device_code,
                    "updated_ts": utc_now_ts(),
                })
            await update_db(mutate)

            await query.edit_message_text(
                payment_text(GAMES[game_id]["title"], DEVICES[device_code]),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ تغيير اللعبة", callback_data="menu:games")]]),
            )
            return

        if data.startswith("admin:"):
            if user_id != ADMIN_CHAT_ID:
                await query.answer("غير مسموح", show_alert=True)
                return

            _, action, order_id = data.split(":", 2)
            order = db.get("pending_payments", {}).get(order_id)

            if not order:
                await query.edit_message_caption("⚠️ الطلب غير موجود أو تمت معالجته سابقًا.")
                return

            target_user_id = int(order["user_id"])
            game_id = order["game"]
            device_code = order["device"]
            game_title = GAMES.get(game_id, {}).get("title", game_id)
            device_title = DEVICES.get(device_code, device_code)

            if action == "info":
                await query.answer(
                    f"Order: {order_id}\nUser: {mask_user_id(target_user_id)}\nGame: {game_title}\nDevice: {device_title}",
                    show_alert=True,
                )
                return

            if action == "reject":
                def mutate(data_obj):
                    current = data_obj["pending_payments"].pop(order_id, None)
                    if current:
                        current["status"] = "rejected"
                        current["reviewed_at"] = iso_now()
                        data_obj["orders"][order_id] = current
                    data_obj["stats"]["rejected_orders"] = int(data_obj["stats"].get("rejected_orders", 0)) + 1
                await update_db(mutate)

                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=(
                        "❌ تم رفض إيصال الدفع.\n\n"
                        "يرجى التأكد من الإيصال أو التواصل معنا للدعم:\n"
                        f"{SUPPORT_URL}"
                    ),
                )

                order["status"] = "rejected"
                await query.edit_message_caption(
                    order_caption(order) + f"\n\n🚫 تم الرفض بواسطة الأدمن في {now_text()}",
                    parse_mode="HTML",
                )
                return

            if action == "approve":
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text="✅ تم قبول الدفع.\nجاري تجهيز رابط التحميل المؤقت...",
                )

                try:
                    download_url = await generate_download_link(target_user_id, game_id, device_code, order_id)
                    if not download_url:
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text="❌ فشل توليد رابط التحميل. تواصل مع الدعم وسيتم حل المشكلة.",
                        )
                        await query.answer("فشل توليد الرابط", show_alert=True)
                        return

                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=(
                            f"🔗 رابط تحميل لعبة <b>{escape_text(game_title)}</b>\n"
                            f"📲 الجهاز: <b>{escape_text(device_title)}</b>\n\n"
                            f"{escape_text(download_url)}\n\n"
                            "⚠️ الرابط صالح لمدة قصيرة فقط، لا تشاركه مع أحد."
                        ),
                        parse_mode="HTML",
                    )

                    def mutate(data_obj):
                        current = data_obj["pending_payments"].pop(order_id, None)
                        if current:
                            current["status"] = "approved"
                            current["reviewed_at"] = iso_now()
                            current["download_url_generated"] = True
                            data_obj["orders"][order_id] = current
                        data_obj["sessions"].pop(user_key(target_user_id), None)
                        data_obj["stats"]["approved_orders"] = int(data_obj["stats"].get("approved_orders", 0)) + 1
                        data_obj["stats"]["generated_links"] = int(data_obj["stats"].get("generated_links", 0)) + 1
                    await update_db(mutate)

                    order["status"] = "approved"
                    await query.edit_message_caption(
                        order_caption(order) + f"\n\n✅ تم القبول وإرسال الرابط في {now_text()}",
                        parse_mode="HTML",
                    )
                    return

                except Exception as error:
                    logger.exception("Approve failed for order %s: %s", order_id, error)
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text="⚠️ تم قبول الدفع، لكن حدث خطأ أثناء توليد الرابط. سيتم التواصل معك قريبًا.",
                    )
                    await query.answer("حدث خطأ أثناء توليد الرابط", show_alert=True)
                    return

        await query.answer("خيار غير معروف", show_alert=True)

    except Exception as error:
        logger.exception("button_handler error: %s", error)
        try:
            await query.answer("حدث خطأ أثناء معالجة الطلب", show_alert=True)
        except Exception:
            pass


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.id != ADMIN_CHAT_ID:
        return

    await cleanup_expired_data()
    stats = db.get("stats", {})
    pending_count = len(db.get("pending_payments", {}))
    sessions_count = len(db.get("sessions", {}))
    total_orders = len(db.get("orders", {}))

    await update.message.reply_text(
        "📊 إحصائيات PlayZone Bot\n\n"
        f"🧾 الطلبات الكلية: {total_orders}\n"
        f"⏳ قيد المراجعة: {pending_count}\n"
        f"👥 جلسات نشطة: {sessions_count}\n"
        f"✅ المقبولة: {stats.get('approved_orders', 0)}\n"
        f"❌ المرفوضة: {stats.get('rejected_orders', 0)}\n"
        f"🔗 روابط مولدة: {stats.get('generated_links', 0)}"
    )


async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.id != ADMIN_CHAT_ID:
        return

    pending = list(db.get("pending_payments", {}).values())
    if not pending:
        await update.message.reply_text("لا توجد طلبات قيد المراجعة حاليًا.")
        return

    lines = ["⏳ الطلبات قيد المراجعة:\n"]
    for order in pending[:20]:
        game_title = GAMES.get(order.get("game"), {}).get("title", order.get("game"))
        device_title = DEVICES.get(order.get("device"), order.get("device"))
        lines.append(
            f"• {order.get('order_id')} | {escape_text(order.get('full_name'))} | {game_title} | {device_title}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)


# ---------------------- Main -------------------------
async def main():
    global db

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. ضع التوكن في Environment Variables باسم BOT_TOKEN.")

    db = load_db_sync()
    await cleanup_expired_data()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("pending", admin_pending))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handler)

    logger.info("PlayZone bot is running...")
    await application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))

        keep_alive()
        nest_asyncio.apply()

        logger.info("Starting PlayZone bot...")
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as error:
        logger.exception("General error: %s", error)
