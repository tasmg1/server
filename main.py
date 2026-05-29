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
import re
from pathlib import Path
from datetime import datetime, timezone
from threading import Thread
from typing import Any, Dict, Optional, List

import aiohttp
import nest_asyncio
from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
)
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
# PlayZone Telegram Bot
# Professional & Secure Version
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("playzone-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID", "8569699093").strip()
DOWNLOAD_API_URL = os.getenv(
    "DOWNLOAD_API_URL",
    "https://gfdbgta.pythonanywhere.com/generate_link"
).strip()
DOWNLOAD_API_SECRET = os.getenv("DOWNLOAD_API_SECRET", "").strip()

SUPPORT_URL = os.getenv("SUPPORT_URL", "https://instagram.com/p1ay.zone").strip()
PAYMENT_CARD = os.getenv("PAYMENT_CARD", "7113282938").strip()
GAME_PRICE = os.getenv("GAME_PRICE", "1000 IQD").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DB_FILE = DATA_DIR / "playzone_bot_data.json"

PAYMENT_TIMEOUT_SECONDS = int(os.getenv("PAYMENT_TIMEOUT_SECONDS", str(60 * 60 * 24)))
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", str(60 * 60 * 2)))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "3"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
DOWNLOAD_LINK_EXPIRE_MINUTES = int(os.getenv("DOWNLOAD_LINK_EXPIRE_MINUTES", "10"))

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
except ValueError:
    raise RuntimeError("ADMIN_CHAT_ID must be an integer")


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_CHAT_ID)


# =====================================================
# Keep Alive Web Server
# =====================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "✅ PlayZone Bot is alive and running!"


def run_web_server():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def keep_alive():
    thread = Thread(target=run_web_server, daemon=True)
    thread.start()


# =====================================================
# Games Data
# =====================================================

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

STATUS_LABELS = {
    "pending": "⏳ قيد المراجعة",
    "approved": "✅ مقبول",
    "rejected": "❌ مرفوض",
    "expired": "⌛ منتهي",
    "link_failed": "⚠️ فشل تجهيز الرابط",
}

REJECTION_REASONS = {
    "unclear": "الإيصال غير واضح",
    "wrong_amount": "المبلغ غير صحيح",
    "not_received": "لم يصل التحويل",
    "invalid_image": "صورة غير صالحة",
}


# =====================================================
# Simple JSON Database
# =====================================================

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
        "link_failures": 0,
        "order_counter": 1000,
    },
}

_db_lock = asyncio.Lock()
db: Dict[str, Any] = json.loads(json.dumps(DEFAULT_DB))


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
    return text[:2] + "***" + text[-2:]


def extract_price_number() -> int:
    digits = re.sub(r"[^0-9]", "", GAME_PRICE)
    return int(digits or "0")


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
            payment = data["pending_payments"].pop(key, None)
            if payment:
                payment["status"] = "expired"
                payment["expired_at"] = iso_now()
                data["orders"][key] = payment

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
        logger.info(
            "Cleaned expired data: payments=%s sessions=%s",
            removed_payments,
            removed_sessions,
        )


async def is_rate_limited(user_id: int, action: str) -> bool:
    now = utc_now_ts()
    key = str(user_id) + ":" + action

    def mutate(data):
        last = int(data["rate_limits"].get(key, 0))

        if now - last < RATE_LIMIT_SECONDS:
            return True

        data["rate_limits"][key] = now
        return False

    return await update_db(mutate)


async def next_order_id() -> str:
    def mutate(data):
        current = int(data["stats"].get("order_counter", 1000)) + 1
        data["stats"]["order_counter"] = current
        return "PZ-" + str(current)

    return await update_db(mutate)


def get_user_orders(user_id: int) -> List[Dict[str, Any]]:
    orders = [
        order for order in db.get("orders", {}).values()
        if str(order.get("user_id")) == str(user_id)
    ]
    orders.sort(key=lambda item: int(item.get("created_ts", 0)), reverse=True)
    return orders


def get_user_latest_order(user_id: int) -> Optional[Dict[str, Any]]:
    orders = get_user_orders(user_id)
    return orders[0] if orders else None


def get_pending_user_order(user_id: int) -> Optional[Dict[str, Any]]:
    for order in db.get("pending_payments", {}).values():
        if str(order.get("user_id")) == str(user_id) and order.get("status") == "pending":
            return order
    return None


# =====================================================
# Keyboards
# =====================================================

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 الألعاب", callback_data="menu:games")],
        [InlineKeyboardButton("📦 حالة طلبي", callback_data="menu:status")],
        [InlineKeyboardButton("🕹️ ألعابي السابقة", callback_data="menu:my_games")],
        [InlineKeyboardButton("💳 طريقة الدفع", callback_data="menu:payment")],
        [InlineKeyboardButton("📞 الدعم", callback_data="menu:support")],
    ])


def games_keyboard() -> InlineKeyboardMarkup:
    buttons = []

    for game_id, game in GAMES.items():
        buttons.append([
            InlineKeyboardButton(
                game["emoji"] + " " + game["title"],
                callback_data="game:" + game_id,
            )
        ])

    buttons.append([InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")])
    return InlineKeyboardMarkup(buttons)


def devices_keyboard(game_id: str) -> InlineKeyboardMarkup:
    buttons = []
    available = GAMES[game_id].get("available_devices", [])

    for device_code in available:
        buttons.append([
            InlineKeyboardButton(
                DEVICES[device_code],
                callback_data="device:" + game_id + ":" + device_code,
            )
        ])

    buttons.append([InlineKeyboardButton("⬅️ اختيار لعبة أخرى", callback_data="menu:games")])
    return InlineKeyboardMarkup(buttons)


def admin_review_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ قبول وإرسال الرابط", callback_data="admin:approve:" + order_id),
            InlineKeyboardButton("❌ رفض", callback_data="admin:reject_menu:" + order_id),
        ],
        [InlineKeyboardButton("ℹ️ معلومات الطلب", callback_data="admin:info:" + order_id)],
    ])


def rejection_reasons_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ الإيصال غير واضح", callback_data="admin:reject_reason:" + order_id + ":unclear")],
        [InlineKeyboardButton("❌ المبلغ غير صحيح", callback_data="admin:reject_reason:" + order_id + ":wrong_amount")],
        [InlineKeyboardButton("❌ لم يصل التحويل", callback_data="admin:reject_reason:" + order_id + ":not_received")],
        [InlineKeyboardButton("❌ صورة غير صالحة", callback_data="admin:reject_reason:" + order_id + ":invalid_image")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="admin:back:" + order_id)],
    ])


def back_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]
    ])


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 تواصل عبر إنستغرام", url=SUPPORT_URL)],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")],
    ])


def download_keyboard(download_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ تحميل اللعبة الآن", url=download_url)],
        [InlineKeyboardButton("📞 أحتاج مساعدة", callback_data="menu:support")],
    ])


# =====================================================
# Text Builders
# =====================================================

def payment_text(game_title: Optional[str] = None, device_title: Optional[str] = None) -> str:
    lines = [
        "💳 <b>إتمام الدفع</b>",
        "",
    ]

    if game_title and device_title:
        lines.append("🎮 <b>اللعبة:</b> " + escape_text(game_title))
        lines.append("📱 <b>الجهاز:</b> " + escape_text(device_title))

    lines.extend([
        "💰 <b>السعر:</b> " + escape_text(GAME_PRICE),
        "",
        "يرجى تحويل المبلغ إلى بطاقة ماستر كارد التالية:",
        "<code>" + escape_text(PAYMENT_CARD) + "</code>",
        "",
        "📸 بعد التحويل، أرسل صورة إيصال الدفع هنا في المحادثة.",
        "",
        "✅ بعد مراجعة الإيصال والموافقة عليه، سيصلك رابط تحميل مؤقت خاص بك.",
        "",
        "⚠️ تأكد أن الإيصال واضح ويظهر مبلغ التحويل.",
    ])

    return "\n".join(lines)


def order_status_text(order: Dict[str, Any]) -> str:
    game_id = order.get("game")
    device_code = order.get("device")
    game_title = GAMES.get(game_id, {}).get("title", game_id)
    device_title = DEVICES.get(device_code, device_code)
    status = order.get("status", "pending")
    status_label = STATUS_LABELS.get(status, status)
    order_id = order.get("order_id", "غير معروف")

    lines = [
        "📦 <b>حالة الطلب</b>",
        "",
        "🧾 <b>رقم الطلب:</b> " + escape_text(order_id),
        "🎮 <b>اللعبة:</b> " + escape_text(game_title),
        "📱 <b>الجهاز:</b> " + escape_text(device_title),
        "💰 <b>السعر:</b> " + escape_text(GAME_PRICE),
        "📌 <b>الحالة:</b> " + escape_text(status_label),
    ]

    if order.get("rejection_reason"):
        lines.append("❗ <b>سبب الرفض:</b> " + escape_text(order.get("rejection_reason")))

    if order.get("created_at_text"):
        lines.append("🕒 <b>وقت الطلب:</b> " + escape_text(order.get("created_at_text")))

    return "\n".join(lines)


def order_caption(order: Dict[str, Any]) -> str:
    game_id = order.get("game")
    device_code = order.get("device")

    game_title = GAMES.get(game_id, {}).get("title", game_id)
    device_title = DEVICES.get(device_code, device_code)
    username = order.get("username") or "لا يوجد"

    if username != "لا يوجد":
        username = "@" + username

    return (
        "📩 <b>طلب دفع جديد</b>\n\n"
        "🧾 رقم الطلب: <code>" + escape_text(order.get("order_id")) + "</code>\n"
        "👤 المستخدم: " + escape_text(order.get("full_name")) + "\n"
        "🔗 username: " + escape_text(username) + "\n"
        "🆔 ID: <code>" + escape_text(order.get("user_id")) + "</code>\n"
        "🎮 اللعبة: " + escape_text(game_title) + "\n"
        "📱 الجهاز: " + escape_text(device_title) + "\n"
        "💰 السعر: " + escape_text(GAME_PRICE) + "\n"
        "🕒 الوقت: " + escape_text(order.get("created_at_text")) + "\n"
        "📌 الحالة: " + escape_text(STATUS_LABELS.get(order.get("status", "pending"), order.get("status", "pending")))
    )


def install_instructions_text(device_code: str) -> str:
    if device_code == "android":
        return (
            "📲 <b>طريقة تثبيت اللعبة على Android</b>\n\n"
            "1️⃣ حمّل ملف APK من الرابط.\n"
            "2️⃣ افتح الملف بعد انتهاء التحميل.\n"
            "3️⃣ إذا ظهر تحذير، فعّل التثبيت من مصادر غير معروفة.\n"
            "4️⃣ اضغط تثبيت.\n"
            "5️⃣ افتح اللعبة واستمتع 🎮"
        )

    return (
        "🍎 <b>ملاحظة iPhone</b>\n\n"
        "إذا لم يعمل التحميل على iPhone، تواصل مع الدعم ليتم إرشادك للطريقة المناسبة."
    )


def support_text(user_id: int) -> str:
    latest = get_user_latest_order(user_id)

    lines = [
        "📞 <b>الدعم</b>",
        "",
        "إذا واجهت مشكلة، أرسل لنا تفاصيل المشكلة عبر زر الدعم بالأسفل.",
    ]

    if latest:
        lines.extend([
            "",
            "بيانات آخر طلب لديك:",
            "🧾 رقم الطلب: " + escape_text(latest.get("order_id")),
            "🎮 اللعبة: " + escape_text(GAMES.get(latest.get("game"), {}).get("title", latest.get("game"))),
            "📱 الجهاز: " + escape_text(DEVICES.get(latest.get("device"), latest.get("device"))),
            "📌 الحالة: " + escape_text(STATUS_LABELS.get(latest.get("status", "pending"), latest.get("status", "pending"))),
        ])

    return "\n".join(lines)


def my_games_text(user_id: int) -> str:
    orders = [
        order for order in get_user_orders(user_id)
        if order.get("status") == "approved"
    ]

    if not orders:
        return "🕹️ لا توجد ألعاب مدفوعة سابقة في حسابك حتى الآن."

    lines = [
        "🕹️ <b>ألعابك السابقة</b>",
        "",
    ]

    for index, order in enumerate(orders[:10], start=1):
        game_title = GAMES.get(order.get("game"), {}).get("title", order.get("game"))
        device_title = DEVICES.get(order.get("device"), order.get("device"))
        lines.append(
            str(index) + ". " +
            escape_text(game_title) + " - " +
            escape_text(device_title) + " - ✅ مدفوع"
        )

    lines.append("")
    lines.append("للحصول على رابط جديد، تواصل مع الدعم أو أرسل طلبًا جديدًا.")

    return "\n".join(lines)


# =====================================================
# Download API
# =====================================================

def sign_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not DOWNLOAD_API_SECRET:
        return payload

    message = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":")
    ).encode("utf-8")

    signature = hmac.new(
        DOWNLOAD_API_SECRET.encode("utf-8"),
        message,
        "sha256"
    ).hexdigest()

    payload["signature"] = signature
    return payload


async def generate_download_link(
    user_id: int,
    game_id: str,
    device_code: str,
    order_id: str,
) -> Optional[str]:
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
                logger.error(
                    "Download API error status=%s body=%s",
                    response.status,
                    body[:500],
                )
                return None

            data = await response.json()
            return data.get("download_url")


# =====================================================
# Bot Commands Setup
# =====================================================

async def setup_bot_commands(application):
    await application.bot.set_my_commands(
        [
            BotCommand("start", "فتح القائمة الرئيسية"),
        ],
        scope=BotCommandScopeDefault(),
    )

    await application.bot.set_my_commands(
        [
            BotCommand("start", "فتح القائمة الرئيسية"),
            BotCommand("stats", "إحصائيات البوت"),
            BotCommand("pending", "الطلبات قيد المراجعة"),
            BotCommand("orders", "آخر الطلبات"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_CHAT_ID),
    )


# =====================================================
# Bot Handlers
# =====================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_expired_data()

    if not update.message:
        return

    user = update.message.from_user
    key = user_key(user.id)

    def mutate(data):
        if not is_admin(user.id):
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
        payload = args[0].strip().lower().replace("-", "").replace("_", "")

        if payload in GAMES:
            def set_game(data):
                session = data["sessions"].setdefault(key, {})
                session.update({
                    "game": payload,
                    "updated_ts": utc_now_ts(),
                })

            await update_db(set_game)

            game = GAMES[payload]

            await update.message.reply_text(
                "👋 أهلاً بك في <b>PlayZone</b>\n\n"
                "تم اختيار لعبة:\n"
                + game["emoji"] + " <b>" + escape_text(game["title"]) + "</b>\n\n"
                "اختر نوع جهازك:",
                parse_mode="HTML",
                reply_markup=devices_keyboard(payload),
            )
            return

    await update.message.reply_text(
        "👋 أهلاً بك في <b>PlayZone</b>\n\n"
        "من هنا يمكنك طلب تحميل الألعاب بعد الدفع.\n\n"
        "الخطوات:\n"
        "1️⃣ اختر اللعبة.\n"
        "2️⃣ اختر نوع جهازك.\n"
        "3️⃣ ادفع " + escape_text(GAME_PRICE) + ".\n"
        "4️⃣ أرسل صورة الإيصال.\n\n"
        "بعد الموافقة، سيصلك رابط تحميل مؤقت خاص بك.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if await is_rate_limited(update.message.from_user.id, "text"):
        return

    await update.message.reply_text(
        "يرجى استخدام الأزرار في الأسفل لاختيار اللعبة ونوع الجهاز.\n\n"
        "بعد الدفع، أرسل صورة الإيصال هنا.",
        reply_markup=main_menu_keyboard(),
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.message.from_user
    key = user_key(user.id)

    if is_admin(user.id):
        await update.message.reply_text(
            "👑 وضع الأدمن مفعّل.\nلن يتم احتساب هذه الصورة كطلب حقيقي."
        )
        return

    if await is_rate_limited(user.id, "photo"):
        await update.message.reply_text("⏳ انتظر قليلًا قبل إرسال إيصال آخر.")
        return

    session = db.get("sessions", {}).get(key, {})
    game_id = session.get("game")
    device_code = session.get("device")

    if not game_id or not device_code:
        await update.message.reply_text(
            "⚠️ يرجى اختيار اللعبة ونوع الجهاز أولًا قبل إرسال إيصال الدفع.",
            reply_markup=games_keyboard(),
        )
        return

    if game_id not in GAMES or device_code not in DEVICES:
        await update.message.reply_text(
            "❌ اختيار اللعبة أو الجهاز غير صحيح. ابدأ من جديد.",
            reply_markup=games_keyboard(),
        )
        return

    existing_pending = get_pending_user_order(user.id)

    if existing_pending:
        await update.message.reply_text(
            "⏳ لديك طلب قيد المراجعة حاليًا.\n\n"
            + order_status_text(existing_pending)
            + "\n\nيرجى انتظار مراجعة الإدارة.",
            parse_mode="HTML",
        )
        return

    file_id = update.message.photo[-1].file_id
    order_id = await next_order_id()

    order = {
        "order_id": order_id,
        "file_id": file_id,
        "game": game_id,
        "device": device_code,
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username or "",
        "status": "pending",
        "price": GAME_PRICE,
        "created_ts": utc_now_ts(),
        "created_at": iso_now(),
        "created_at_text": now_text(),
    }

    def mutate(data):
        data["pending_payments"][order_id] = order
        data["orders"][order_id] = order.copy()
        data["stats"]["submitted_receipts"] = int(
            data["stats"].get("submitted_receipts", 0)
        ) + 1

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
        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء إرسال الإيصال للمراجعة. حاول لاحقًا."
        )
        return

    await update.message.reply_text(
        "✅ تم استلام إيصال الدفع بنجاح.\n\n"
        "🧾 رقم الطلب: " + escape_text(order_id) + "\n\n"
        "سيتم مراجعته من الإدارة قريبًا.\n"
        "بعد الموافقة، سيصلك رابط التحميل المؤقت هنا مباشرة.",
        parse_mode="HTML",
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

        if data == "menu:status":
            latest = get_user_latest_order(user_id)

            if not latest:
                await query.edit_message_text(
                    "📦 لا يوجد لديك طلبات حتى الآن.\n\nابدأ باختيار لعبة من القائمة.",
                    reply_markup=main_menu_keyboard(),
                )
                return

            await query.edit_message_text(
                order_status_text(latest),
                parse_mode="HTML",
                reply_markup=back_home_keyboard(),
            )
            return

        if data == "menu:my_games":
            await query.edit_message_text(
                my_games_text(user_id),
                parse_mode="HTML",
                reply_markup=back_home_keyboard(),
            )
            return

        if data == "menu:support":
            await query.edit_message_text(
                support_text(user_id),
                parse_mode="HTML",
                reply_markup=support_keyboard(),
            )
            return

        if data.startswith("game:"):
            game_id = data.split(":", 1)[1]

            if game_id not in GAMES:
                await query.edit_message_text("❌ هذه اللعبة غير متوفرة حاليًا.")
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
                game["emoji"] + " <b>" + escape_text(game["title"]) + "</b>\n"
                + escape_text(game["description"]) + "\n\n"
                + "اختر نوع جهازك:",
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
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ تغيير اللعبة", callback_data="menu:games")]
                ]),
            )
            return

        if data.startswith("admin:"):
            await handle_admin_callback(query, context, data, user_id)
            return

        await query.answer("خيار غير معروف", show_alert=True)

    except Exception as error:
        logger.exception("button_handler error: %s", error)
        try:
            await query.answer("حدث خطأ أثناء معالجة الطلب", show_alert=True)
        except Exception:
            pass


async def handle_admin_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str, user_id: int):
    if user_id != ADMIN_CHAT_ID:
        await query.answer("غير مسموح", show_alert=True)
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    order_id = parts[2] if len(parts) > 2 else ""

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
            "Order: " + order_id + "\n"
            "User: " + mask_user_id(target_user_id) + "\n"
            "Game: " + str(game_title) + "\n"
            "Device: " + str(device_title),
            show_alert=True,
        )
        return

    if action == "reject_menu":
        await query.edit_message_caption(
            order_caption(order) + "\n\nاختر سبب رفض الإيصال:",
            parse_mode="HTML",
            reply_markup=rejection_reasons_keyboard(order_id),
        )
        return

    if action == "back":
        await query.edit_message_caption(
            order_caption(order),
            parse_mode="HTML",
            reply_markup=admin_review_keyboard(order_id),
        )
        return

    if action == "reject_reason":
        reason_key = parts[3] if len(parts) > 3 else "invalid_image"
        reason_text = REJECTION_REASONS.get(reason_key, "تم رفض الإيصال")

        def mutate(data_obj):
            current = data_obj["pending_payments"].pop(order_id, None)

            if current:
                current["status"] = "rejected"
                current["reviewed_at"] = iso_now()
                current["rejection_reason"] = reason_text
                data_obj["orders"][order_id] = current

            data_obj["stats"]["rejected_orders"] = int(
                data_obj["stats"].get("rejected_orders", 0)
            ) + 1

        await update_db(mutate)

        await context.bot.send_message(
            chat_id=target_user_id,
            text=(
                "❌ تم رفض إيصال الدفع.\n\n"
                "🧾 رقم الطلب: " + escape_text(order_id) + "\n"
                "السبب: " + escape_text(reason_text) + "\n\n"
                "يرجى إرسال إيصال صحيح أو التواصل مع الدعم:\n"
                + SUPPORT_URL
            ),
        )

        order["status"] = "rejected"
        order["rejection_reason"] = reason_text

        await query.edit_message_caption(
            order_caption(order)
            + "\n\n🚫 تم الرفض بواسطة الإدارة في "
            + now_text()
            + "\nالسبب: "
            + escape_text(reason_text),
            parse_mode="HTML",
        )
        return

    if action == "approve":
        await context.bot.send_message(
            chat_id=target_user_id,
            text="✅ تم قبول الدفع بنجاح.\n\nجاري تجهيز رابط التحميل المؤقت الخاص بك...",
        )

        try:
            download_url = await generate_download_link(
                target_user_id,
                game_id,
                device_code,
                order_id,
            )

            if not download_url:
                def fail_mutate(data_obj):
                    current = data_obj["pending_payments"].get(order_id)
                    if current:
                        current["status"] = "link_failed"
                        data_obj["orders"][order_id] = current.copy()

                    data_obj["stats"]["link_failures"] = int(
                        data_obj["stats"].get("link_failures", 0)
                    ) + 1

                await update_db(fail_mutate)

                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=(
                        "❌ حدث خطأ أثناء تجهيز رابط التحميل.\n"
                        "يرجى التواصل مع الدعم وسيتم حل المشكلة."
                    ),
                )

                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        "⚠️ <b>فشل توليد رابط تحميل</b>\n\n"
                        "🧾 الطلب: <code>" + escape_text(order_id) + "</code>\n"
                        "🎮 اللعبة: " + escape_text(game_title) + "\n"
                        "📱 الجهاز: " + escape_text(device_title) + "\n"
                        "السبب: سيرفر التحميل لم يرجع download_url."
                    ),
                    parse_mode="HTML",
                )

                await query.answer("فشل توليد الرابط", show_alert=True)
                return

            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "✅ <b>تم قبول طلبك</b>\n\n"
                    "🧾 رقم الطلب: <code>" + escape_text(order_id) + "</code>\n"
                    "🎮 اللعبة: <b>" + escape_text(game_title) + "</b>\n"
                    "📱 الجهاز: <b>" + escape_text(device_title) + "</b>\n\n"
                    "اضغط الزر بالأسفل لتحميل اللعبة.\n\n"
                    "⚠️ الرابط مؤقت وصالح لمدة قصيرة فقط.\n"
                    "لا تشاركه مع أي شخص."
                ),
                parse_mode="HTML",
                reply_markup=download_keyboard(download_url),
            )

            await context.bot.send_message(
                chat_id=target_user_id,
                text=install_instructions_text(device_code),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📞 أحتاج مساعدة", callback_data="menu:support")]
                ]),
            )

            def mutate(data_obj):
                current = data_obj["pending_payments"].pop(order_id, None)

                if current:
                    current["status"] = "approved"
                    current["reviewed_at"] = iso_now()
                    current["download_url_generated"] = True
                    current["download_url"] = download_url
                    data_obj["orders"][order_id] = current

                data_obj["sessions"].pop(user_key(target_user_id), None)

                data_obj["stats"]["approved_orders"] = int(
                    data_obj["stats"].get("approved_orders", 0)
                ) + 1

                data_obj["stats"]["generated_links"] = int(
                    data_obj["stats"].get("generated_links", 0)
                ) + 1

            await update_db(mutate)

            order["status"] = "approved"

            await query.edit_message_caption(
                order_caption(order) + "\n\n✅ تم القبول وإرسال الرابط في " + now_text(),
                parse_mode="HTML",
            )
            return

        except Exception as error:
            logger.exception("Approve failed for order %s: %s", order_id, error)

            def fail_mutate(data_obj):
                current = data_obj["pending_payments"].get(order_id)
                if current:
                    current["status"] = "link_failed"
                    data_obj["orders"][order_id] = current.copy()

                data_obj["stats"]["link_failures"] = int(
                    data_obj["stats"].get("link_failures", 0)
                ) + 1

            await update_db(fail_mutate)

            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "⚠️ تم قبول الدفع، لكن حدث خطأ أثناء تجهيز رابط التحميل.\n"
                    "سيتم حل المشكلة والتواصل معك قريبًا."
                ),
            )

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "⚠️ <b>خطأ أثناء توليد الرابط</b>\n\n"
                    "🧾 الطلب: <code>" + escape_text(order_id) + "</code>\n"
                    "🎮 اللعبة: " + escape_text(game_title) + "\n"
                    "📱 الجهاز: " + escape_text(device_title) + "\n"
                    "الخطأ: " + escape_text(str(error))
                ),
                parse_mode="HTML",
            )

            await query.answer("حدث خطأ أثناء توليد الرابط", show_alert=True)
            return


# =====================================================
# Admin Commands
# =====================================================

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.id != ADMIN_CHAT_ID:
        return

    await cleanup_expired_data()

    stats = db.get("stats", {})
    pending_count = len(db.get("pending_payments", {}))
    sessions_count = len(db.get("sessions", {}))
    total_orders = len(db.get("orders", {}))
    sales = int(stats.get("approved_orders", 0)) * extract_price_number()

    await update.message.reply_text(
        "📊 إحصائيات PlayZone Bot\n\n"
        "🧾 الطلبات الكلية: " + str(total_orders) + "\n"
        "⏳ قيد المراجعة: " + str(pending_count) + "\n"
        "👥 جلسات نشطة: " + str(sessions_count) + "\n"
        "✅ المقبولة: " + str(stats.get("approved_orders", 0)) + "\n"
        "❌ المرفوضة: " + str(stats.get("rejected_orders", 0)) + "\n"
        "🔗 روابط مولدة: " + str(stats.get("generated_links", 0)) + "\n"
        "⚠️ فشل الروابط: " + str(stats.get("link_failures", 0)) + "\n"
        "💰 المبيعات التقريبية: " + str(sales) + " IQD"
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
            "• "
            + str(order.get("order_id"))
            + " | "
            + str(order.get("full_name"))
            + " | "
            + str(game_title)
            + " | "
            + str(device_title)
        )

    await update.message.reply_text("\n".join(lines))


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.id != ADMIN_CHAT_ID:
        return

    orders = list(db.get("orders", {}).values())
    orders.sort(key=lambda item: int(item.get("created_ts", 0)), reverse=True)

    if not orders:
        await update.message.reply_text("لا توجد طلبات محفوظة حتى الآن.")
        return

    lines = ["🧾 آخر الطلبات:\n"]

    for order in orders[:10]:
        game_title = GAMES.get(order.get("game"), {}).get("title", order.get("game"))
        device_title = DEVICES.get(order.get("device"), order.get("device"))
        status = STATUS_LABELS.get(order.get("status", "pending"), order.get("status", "pending"))

        lines.append(
            str(order.get("order_id"))
            + " | "
            + str(game_title)
            + " | "
            + str(device_title)
            + " | "
            + str(status)
        )

    await update.message.reply_text("\n".join(lines))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)


# =====================================================
# Main
# =====================================================

async def main():
    global db

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it in Railway Variables as BOT_TOKEN.")

    db = load_db_sync()
    await cleanup_expired_data()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    await setup_bot_commands(application)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("pending", admin_pending))
    application.add_handler(CommandHandler("orders", admin_orders))

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
