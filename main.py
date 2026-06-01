import os
import sys
import json
import time
import hmac
import html
import signal
import asyncio
import logging
import re
from pathlib import Path
from datetime import datetime, timezone
from threading import Thread
from typing import Any, Dict, Optional, List, Tuple
 
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
from telegram.error import TelegramError, BadRequest
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
# Ultra Professional Version
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
    "https://gfdbgta.pythonanywhere.com/generate_link",
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
except ValueError as exc:
    raise RuntimeError("ADMIN_CHAT_ID must be an integer") from exc

def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_CHAT_ID)

# =====================================================
# Keep Alive Web Server for Railway
# =====================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ PlayZone Bot is alive and running!"

@app.route("/health")
def health():
    return {
        "success": True,
        "server": "online",
        "bot": "PlayZone",
        "time": iso_now(),
    }

def run_web_server():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    thread = Thread(target=run_web_server, daemon=True)
    thread.start()

# =====================================================
# Store Data
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
    "draft": "📝 قيد الاختيار",
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

DEFAULT_DB = {
    "pending_payments": {},
    "sessions": {},
    "orders": {},
    "users": {},
    "rate_limits": {},
    "audit_log": [],
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

# =====================================================
# Helpers
# =====================================================

def utc_now_ts() -> int:
    return int(time.time())

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def user_key(user_id: int) -> str:
    return str(user_id)

def escape_text(value: Any) -> str:
    return html.escape(str(value or ""))

def extract_price_number() -> int:
    digits = re.sub(r"[^0-9]", "", GAME_PRICE)
    return int(digits or "0")

def mask_user_id(user_id: int) -> str:
    value = str(user_id)
    if len(value) <= 4:
        return value
    return value[:2] + "***" + value[-2:]

def normalize_game_payload(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "").replace("_", "")

def get_game_title(game_id: str) -> str:
    return GAMES.get(game_id, {}).get("title", game_id or "غير معروف")

def get_device_title(device_code: str) -> str:
    return DEVICES.get(device_code, device_code or "غير معروف")

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def merge_defaults(base: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    merged = json.loads(json.dumps(base))
    for key, value in loaded.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged

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

    return merge_defaults(DEFAULT_DB, loaded)

def save_db_sync() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = DB_FILE.with_suffix(".tmp")
    backup_file = DB_FILE.with_suffix(".bak")

    try:
        if DB_FILE.exists():
            DB_FILE.replace(backup_file)
    except Exception:
        pass

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(db, file, ensure_ascii=False, indent=2)

    temp_file.replace(DB_FILE)

async def update_db(mutator):
    async with _db_lock:
        result = mutator(db)
        save_db_sync()
        return result

def add_audit(data: Dict[str, Any], action: str, details: Dict[str, Any]) -> None:
    item = {
        "time": iso_now(),
        "action": action,
        "details": details,
    }
    data.setdefault("audit_log", []).append(item)
    data["audit_log"] = data["audit_log"][-200:]

async def cleanup_expired_data() -> None:
    now = utc_now_ts()

    def mutate(data):
        expired_payments = [
            key for key, payment in data["pending_payments"].items()
            if now - safe_int(payment.get("created_ts"), now) > PAYMENT_TIMEOUT_SECONDS
        ]

        for key in expired_payments:
            payment = data["pending_payments"].pop(key, None)
            if payment:
                payment["status"] = "expired"
                payment["expired_at"] = iso_now()
                data["orders"][key] = payment
                add_audit(data, "order_expired", {"order_id": key})

        expired_sessions = [
            key for key, session in data["sessions"].items()
            if now - safe_int(session.get("updated_ts", session.get("created_ts", now)), now) > SESSION_TIMEOUT_SECONDS
        ]

        for key in expired_sessions:
            data["sessions"].pop(key, None)

        old_rate_limits = [
            key for key, ts in data["rate_limits"].items()
            if now - safe_int(ts, now) > RATE_LIMIT_SECONDS * 10
        ]

        for key in old_rate_limits:
            data["rate_limits"].pop(key, None)

        return len(expired_payments), len(expired_sessions)

    removed_payments, removed_sessions = await update_db(mutate)
    if removed_payments or removed_sessions:
        logger.info("Cleaned expired data: payments=%s sessions=%s", removed_payments, removed_sessions)

async def is_rate_limited(user_id: int, action: str) -> bool:
    if is_admin(user_id):
        return False

    now = utc_now_ts()
    key = f"{user_id}:{action}"

    def mutate(data):
        last = safe_int(data["rate_limits"].get(key), 0)
        if now - last < RATE_LIMIT_SECONDS:
            return True
        data["rate_limits"][key] = now
        return False

    return await update_db(mutate)

async def next_order_id() -> str:
    def mutate(data):
        current = safe_int(data["stats"].get("order_counter"), 1000) + 1
        data["stats"]["order_counter"] = current
        return "PZ-" + str(current)

    return await update_db(mutate)

async def register_user(user) -> None:
    if is_admin(user.id):
        return

    key = user_key(user.id)

    def mutate(data):
        users = data.setdefault("users", {})
        is_new = key not in users
        users[key] = {
            "user_id": user.id,
            "full_name": user.full_name,
            "username": user.username or "",
            "first_seen": users.get(key, {}).get("first_seen", iso_now()),
            "last_seen": iso_now(),
        }
        if is_new:
            data["stats"]["started_users"] = safe_int(data["stats"].get("started_users"), 0) + 1
            add_audit(data, "new_user", {"user_id": user.id, "username": user.username or ""})

    await update_db(mutate)

async def upsert_session(user, changes: Dict[str, Any], push_screen: Optional[str] = None) -> Dict[str, Any]:
    key = user_key(user.id)

    def mutate(data):
        sessions = data.setdefault("sessions", {})
        session = sessions.setdefault(key, {
            "user_id": user.id,
            "full_name": user.full_name,
            "username": user.username or "",
            "created_ts": utc_now_ts(),
            "history": [],
        })
        session.update({
            "full_name": user.full_name,
            "username": user.username or "",
            "updated_ts": utc_now_ts(),
        })
        session.update(changes)
        if push_screen:
            history = session.setdefault("history", [])
            if not history or history[-1] != push_screen:
                history.append(push_screen)
                session["history"] = history[-8:]
        return session.copy()

    return await update_db(mutate)

async def set_last_menu_message(user_id: int, message_id: int) -> None:
    key = user_key(user_id)

    def mutate(data):
        session = data.setdefault("sessions", {}).setdefault(key, {
            "user_id": user_id,
            "created_ts": utc_now_ts(),
            "history": [],
        })
        session["last_menu_message_id"] = message_id
        session["updated_ts"] = utc_now_ts()

    await update_db(mutate)

def get_session(user_id: int) -> Dict[str, Any]:
    return db.get("sessions", {}).get(user_key(user_id), {})

async def delete_last_menu_if_possible(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    session = get_session(user_id)
    msg_id = session.get("last_menu_message_id")
    if not msg_id:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=int(msg_id))
    except Exception:
        pass

def get_user_orders(user_id: int) -> List[Dict[str, Any]]:
    orders = [
        order for order in db.get("orders", {}).values()
        if str(order.get("user_id")) == str(user_id)
    ]
    orders.sort(key=lambda item: safe_int(item.get("created_ts"), 0), reverse=True)
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

def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)

def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🎮 الألعاب", callback_data="menu:games"),
            InlineKeyboardButton("📦 حالة طلبي", callback_data="menu:status"),
        ],
        [
            InlineKeyboardButton("🕹️ ألعابي", callback_data="menu:my_games"),
            InlineKeyboardButton("💳 الدفع", callback_data="menu:payment"),
        ],
        [
            InlineKeyboardButton("❓ المساعدة", callback_data="menu:help"),
            InlineKeyboardButton("📞 الدعم", callback_data="menu:support"),
        ],
    ]

    if is_admin(user_id):
        rows.append([InlineKeyboardButton("👑 لوحة الأدمن", callback_data="admin:panel")])

    return kb(rows)

def games_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for game_id, game in GAMES.items():
        rows.append([InlineKeyboardButton(f"{game['emoji']} {game['title']}", callback_data=f"game:{game_id}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")])
    return kb(rows)

def devices_keyboard(game_id: str) -> InlineKeyboardMarkup:
    rows = []
    for device_code in GAMES[game_id].get("available_devices", []):
        rows.append([InlineKeyboardButton(DEVICES[device_code], callback_data=f"device:{game_id}:{device_code}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")])
    return kb(rows)

def payment_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("📸 أرسلت الإيصال", callback_data="pay:receipt_help")],
        [InlineKeyboardButton("🎮 تغيير اللعبة", callback_data="menu:games")],
        [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")],
    ])

def support_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("📞 تواصل عبر إنستغرام", url=SUPPORT_URL)],
        [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")],
    ])

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [
            InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats"),
            InlineKeyboardButton("⏳ الطلبات", callback_data="admin:pending"),
        ],
        [
            InlineKeyboardButton("🧾 آخر الطلبات", callback_data="admin:orders"),
            InlineKeyboardButton("📤 تصدير البيانات", callback_data="admin:export"),
        ],
        [
            InlineKeyboardButton("♻️ تصفير العدادات", callback_data="admin:reset_confirm"),
            InlineKeyboardButton("🧹 حذف الجلسات", callback_data="admin:clear_sessions_confirm"),
        ],
        [
            InlineKeyboardButton("📢 إرسال تنبيه", callback_data="admin:broadcast_prompt"),
            InlineKeyboardButton("🏠 القائمة", callback_data="menu:home"),
        ],
    ])

def admin_review_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [
            InlineKeyboardButton("✅ قبول وإرسال زر التحميل", callback_data=f"admin:approve:{order_id}"),
        ],
        [
            InlineKeyboardButton("❌ رفض الإيصال", callback_data=f"admin:reject_menu:{order_id}"),
            InlineKeyboardButton("ℹ️ معلومات", callback_data=f"admin:info:{order_id}"),
        ],
        [
            InlineKeyboardButton("👑 لوحة الأدمن", callback_data="admin:panel"),
        ],
    ])

def rejection_reasons_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("❌ الإيصال غير واضح", callback_data=f"admin:reject_reason:{order_id}:unclear")],
        [InlineKeyboardButton("❌ المبلغ غير صحيح", callback_data=f"admin:reject_reason:{order_id}:wrong_amount")],
        [InlineKeyboardButton("❌ لم يصل التحويل", callback_data=f"admin:reject_reason:{order_id}:not_received")],
        [InlineKeyboardButton("❌ صورة غير صالحة", callback_data=f"admin:reject_reason:{order_id}:invalid_image")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data=f"admin:back:{order_id}")],
    ])

def download_keyboard(download_url: str, order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("⬇️ تحميل اللعبة الآن", url=download_url)],
        [
            InlineKeyboardButton("📲 طريقة التثبيت", callback_data=f"download:install:{order_id}"),
            InlineKeyboardButton("📦 حالة الطلب", callback_data=f"download:status:{order_id}"),
        ],
        [InlineKeyboardButton("📞 أحتاج مساعدة", callback_data=f"download:support:{order_id}")],
    ])

def download_back_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("⬅️ رجوع لزر التحميل", callback_data=f"download:back:{order_id}")],
    ])

def download_support_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("📞 تواصل عبر إنستغرام", url=SUPPORT_URL)],
        [InlineKeyboardButton("⬅️ رجوع لزر التحميل", callback_data=f"download:back:{order_id}")],
    ])

def receipt_submitted_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("📦 متابعة حالة الطلب", callback_data=f"receipt:status:{order_id}")],
        [InlineKeyboardButton("📞 الدعم", callback_data=f"receipt:support:{order_id}")],
        [InlineKeyboardButton("🎮 اختيار لعبة أخرى", callback_data="menu:games")],
    ])

def receipt_back_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("⬅️ رجوع لرسالة الإيصال", callback_data=f"receipt:back:{order_id}")],
    ])

def order_support_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("📞 تواصل عبر إنستغرام", url=SUPPORT_URL)],
        [InlineKeyboardButton("⬅️ رجوع لرسالة الإيصال", callback_data=f"receipt:back:{order_id}")],
    ])

def confirm_reset_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("✅ نعم، صفّر العدادات", callback_data="admin:reset_stats")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="admin:panel")],
    ])

def confirm_clear_sessions_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("✅ نعم، احذف الجلسات", callback_data="admin:clear_sessions")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="admin:panel")],
    ])

# =====================================================
# Text Builders
# =====================================================

def home_text(user_id: int) -> str:
    admin_line = "\n\n👑 <b>وضع الأدمن مفعل.</b>" if is_admin(user_id) else ""
    return (
        "👋 <b>أهلاً بك في PlayZone</b>\n\n"
        "من هنا يمكنك طلب ألعاب الموبايل بطريقة منظمة وآمنة.\n\n"
        "اختر من الأزرار بالأسفل:"
        + admin_line
    )

def games_text() -> str:
    return (
        "🎮 <b>الألعاب المتوفرة</b>\n\n"
        "اختر اللعبة التي تريد تحميلها:"
    )

def game_details_text(game_id: str) -> str:
    game = GAMES[game_id]
    return (
        f"{game['emoji']} <b>{escape_text(game['title'])}</b>\n\n"
        f"{escape_text(game['description'])}\n\n"
        "اختر نوع جهازك:"
    )

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
        "حوّل المبلغ إلى بطاقة ماستر كارد التالية:",
        "<code>" + escape_text(PAYMENT_CARD) + "</code>",
        "",
        "📸 بعد التحويل، أرسل صورة الإيصال هنا في المحادثة.",
        "",
        "✅ بعد موافقة الإدارة سيصلك زر تحميل مؤقت خاص بك.",
        "⚠️ تأكد أن الإيصال واضح ويظهر مبلغ التحويل.",
    ])

    return "\n".join(lines)

def receipt_help_text() -> str:
    return (
        "📸 <b>إرسال الإيصال</b>\n\n"
        "أرسل صورة إيصال الدفع هنا في نفس المحادثة.\n\n"
        "نصائح لقبول أسرع:\n"
        "• اجعل الصورة واضحة.\n"
        "• يجب أن يظهر مبلغ التحويل.\n"
        "• لا ترسل أكثر من صورة لنفس الطلب.\n"
        "• انتظر مراجعة الإدارة."
    )

def order_status_text(order: Dict[str, Any]) -> str:
    game_title = get_game_title(order.get("game"))
    device_title = get_device_title(order.get("device"))
    status = order.get("status", "pending")
    status_label = STATUS_LABELS.get(status, status)

    lines = [
        "📦 <b>حالة الطلب</b>",
        "",
        "🧾 <b>رقم الطلب:</b> " + escape_text(order.get("order_id", "غير معروف")),
        "🎮 <b>اللعبة:</b> " + escape_text(game_title),
        "📱 <b>الجهاز:</b> " + escape_text(device_title),
        "💰 <b>السعر:</b> " + escape_text(order.get("price", GAME_PRICE)),
        "📌 <b>الحالة:</b> " + escape_text(status_label),
    ]

    if order.get("rejection_reason"):
        lines.append("❗ <b>سبب الرفض:</b> " + escape_text(order.get("rejection_reason")))

    if order.get("created_at_text"):
        lines.append("🕒 <b>وقت الطلب:</b> " + escape_text(order.get("created_at_text")))

    return "\n".join(lines)

def receipt_received_text(order: Dict[str, Any]) -> str:
    return (
        "✅ <b>تم استلام إيصال الدفع بنجاح</b>\n\n"
        "🧾 رقم الطلب: <code>" + escape_text(order.get("order_id", "")) + "</code>\n"
        "🎮 اللعبة: <b>" + escape_text(get_game_title(order.get("game"))) + "</b>\n"
        "📱 الجهاز: <b>" + escape_text(get_device_title(order.get("device"))) + "</b>\n"
        "💰 السعر: <b>" + escape_text(order.get("price", GAME_PRICE)) + "</b>\n\n"
        "📌 الحالة: <b>قيد المراجعة</b>\n"
        "بعد الموافقة سيصلك زر التحميل هنا مباشرة."
    )

def no_order_text() -> str:
    return (
        "📦 <b>حالة طلبي</b>\n\n"
        "لا يوجد لديك طلب حاليًا.\n\n"
        "ابدأ باختيار لعبة من زر <b>الألعاب</b>."
    )

def my_games_text(user_id: int) -> str:
    orders = [order for order in get_user_orders(user_id) if order.get("status") == "approved"]

    if not orders:
        return (
            "🕹️ <b>ألعابي</b>\n\n"
            "لا توجد ألعاب مقبولة سابقًا في حسابك حتى الآن."
        )

    lines = ["🕹️ <b>ألعابك السابقة</b>", ""]
    for index, order in enumerate(orders[:10], start=1):
        game_title = get_game_title(order.get("game"))
        device_title = get_device_title(order.get("device"))
        lines.append(f"{index}. {escape_text(game_title)} - {escape_text(device_title)}")

    lines.append("")
    lines.append("لرابط جديد، اطلب اللعبة مرة أخرى أو تواصل مع الدعم.")
    return "\n".join(lines)

def help_text() -> str:
    return (
        "❓ <b>المساعدة</b>\n\n"
        "طريقة الطلب:\n"
        "1️⃣ اختر اللعبة.\n"
        "2️⃣ اختر نوع جهازك.\n"
        "3️⃣ حوّل مبلغ اللعبة.\n"
        "4️⃣ أرسل صورة الإيصال.\n"
        "5️⃣ بعد الموافقة يصلك زر التحميل.\n\n"
        "🔐 رابط التحميل مؤقت وخاص بك.\n"
        "📌 لا تشارك الرابط مع أي شخص."
    )

def download_ready_text(order: Dict[str, Any]) -> str:
    order_id = order.get("order_id", "")
    game_title = get_game_title(order.get("game"))
    device_title = get_device_title(order.get("device"))
    return (
        "✅ <b>طلبك جاهز</b>\n\n"
        "🧾 رقم الطلب: <code>" + escape_text(order_id) + "</code>\n"
        "🎮 اللعبة: <b>" + escape_text(game_title) + "</b>\n"
        "📱 الجهاز: <b>" + escape_text(device_title) + "</b>\n\n"
        "اضغط زر التحميل بالأسفل.\n"
        "⚠️ الرابط مؤقت وخاص بك، لا تشاركه مع أي شخص."
    )

def support_text(user_id: int) -> str:
    latest = get_user_latest_order(user_id)

    lines = [
        "📞 <b>الدعم</b>",
        "",
        "للمساعدة اضغط زر التواصل بالأسفل.",
    ]

    if latest:
        lines.extend([
            "",
            "آخر طلب لديك:",
            "🧾 " + escape_text(latest.get("order_id")),
            "🎮 " + escape_text(get_game_title(latest.get("game"))),
            "📌 " + escape_text(STATUS_LABELS.get(latest.get("status", "pending"), latest.get("status", "pending"))),
        ])

    return "\n".join(lines)

def install_instructions_text(device_code: Optional[str] = None) -> str:
    if device_code == "ios":
        return (
            "🍎 <b>ملاحظة iPhone</b>\n\n"
            "إذا لم يعمل التحميل على iPhone، تواصل مع الدعم ليتم إرشادك للطريقة المناسبة."
        )

    return (
        "📲 <b>طريقة تثبيت Android</b>\n\n"
        "1️⃣ اضغط زر التحميل.\n"
        "2️⃣ انتظر اكتمال تحميل ملف APK.\n"
        "3️⃣ افتح الملف.\n"
        "4️⃣ إذا ظهر تحذير، فعّل التثبيت من مصادر غير معروفة.\n"
        "5️⃣ اضغط تثبيت ثم افتح اللعبة."
    )

def order_caption(order: Dict[str, Any]) -> str:
    username = order.get("username") or "لا يوجد"
    if username != "لا يوجد":
        username = "@" + username

    return (
        "📩 <b>مراجعة إيصال دفع جديد</b>\n\n"
        "🧾 رقم الطلب: <code>" + escape_text(order.get("order_id")) + "</code>\n"
        "👤 الاسم: " + escape_text(order.get("full_name")) + "\n"
        "🔗 username: " + escape_text(username) + "\n"
        "🆔 ID: <code>" + escape_text(order.get("user_id")) + "</code>\n"
        "🎮 اللعبة: " + escape_text(get_game_title(order.get("game"))) + "\n"
        "📱 الجهاز: " + escape_text(get_device_title(order.get("device"))) + "\n"
        "💰 السعر: " + escape_text(order.get("price", GAME_PRICE)) + "\n"
        "🕒 الوقت: " + escape_text(order.get("created_at_text")) + "\n"
        "📌 الحالة: " + escape_text(STATUS_LABELS.get(order.get("status", "pending"), order.get("status", "pending")))
    )

def admin_stats_text() -> str:
    stats = db.get("stats", {})
    pending_count = len(db.get("pending_payments", {}))
    total_orders = len(db.get("orders", {}))
    active_sessions = len(db.get("sessions", {}))
    user_count = len(db.get("users", {}))
    sales = safe_int(stats.get("approved_orders"), 0) * extract_price_number()

    return (
        "📊 <b>إحصائيات PlayZone</b>\n\n"
        f"👥 المستخدمون: <b>{user_count}</b>\n"
        f"🧾 الطلبات الكلية: <b>{total_orders}</b>\n"
        f"⏳ قيد المراجعة: <b>{pending_count}</b>\n"
        f"✅ المقبولة: <b>{safe_int(stats.get('approved_orders'), 0)}</b>\n"
        f"❌ المرفوضة: <b>{safe_int(stats.get('rejected_orders'), 0)}</b>\n"
        f"🔗 روابط مولدة: <b>{safe_int(stats.get('generated_links'), 0)}</b>\n"
        f"⚠️ فشل الروابط: <b>{safe_int(stats.get('link_failures'), 0)}</b>\n"
        f"🧠 جلسات مؤقتة: <b>{active_sessions}</b>\n"
        f"💰 المبيعات التقريبية: <b>{sales} IQD</b>"
    )

def admin_pending_text() -> str:
    pending = list(db.get("pending_payments", {}).values())
    pending.sort(key=lambda item: safe_int(item.get("created_ts"), 0), reverse=True)

    if not pending:
        return "⏳ <b>الطلبات قيد المراجعة</b>\n\nلا توجد طلبات حاليًا."

    lines = ["⏳ <b>الطلبات قيد المراجعة</b>", ""]
    for order in pending[:20]:
        lines.append(
            f"• <code>{escape_text(order.get('order_id'))}</code> | "
            f"{escape_text(get_game_title(order.get('game')))} | "
            f"{escape_text(get_device_title(order.get('device')))}"
        )
    return "\n".join(lines)

def admin_orders_text() -> str:
    orders = list(db.get("orders", {}).values())
    orders.sort(key=lambda item: safe_int(item.get("created_ts"), 0), reverse=True)

    if not orders:
        return "🧾 <b>آخر الطلبات</b>\n\nلا توجد طلبات محفوظة."

    lines = ["🧾 <b>آخر الطلبات</b>", ""]
    for order in orders[:15]:
        status = STATUS_LABELS.get(order.get("status", "pending"), order.get("status", "pending"))
        lines.append(
            f"{escape_text(order.get('order_id'))} | "
            f"{escape_text(get_game_title(order.get('game')))} | "
            f"{escape_text(status)}"
        )
    return "\n".join(lines)

# =====================================================
# UI Send/Edit Helpers
# =====================================================

async def send_new_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    if not update.message:
        return
    user = update.message.from_user
    chat_id = update.message.chat_id
    await delete_last_menu_if_possible(context, chat_id, user.id)
    msg = await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    await set_last_menu_message(user.id, msg.message_id)

async def edit_query_message(query, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    except BadRequest as error:
        # Message is not modified / old photo caption / etc.
        if "Message is not modified" in str(error):
            return
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)

async def show_home_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await send_new_menu(update, context, home_text(update.message.from_user.id), main_menu_keyboard(update.message.from_user.id))

async def show_home_from_query(query) -> None:
    await edit_query_message(query, home_text(query.from_user.id), main_menu_keyboard(query.from_user.id))

# =====================================================
# Download API
# =====================================================

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
                logger.error("Download API error status=%s body=%s", response.status, body[:500])
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
            BotCommand("cancel", "إلغاء الاختيار الحالي"),
            BotCommand("help", "شرح طريقة الطلب"),
        ],
        scope=BotCommandScopeDefault(),
    )

    await application.bot.set_my_commands(
        [
            BotCommand("start", "فتح القائمة الرئيسية"),
            BotCommand("admin", "لوحة الأدمن"),
            BotCommand("stats", "إحصائيات البوت"),
            BotCommand("pending", "الطلبات قيد المراجعة"),
            BotCommand("orders", "آخر الطلبات"),
            BotCommand("reset_stats", "تصفير عدادات البوت"),
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
    await register_user(user)
    await upsert_session(user, {}, push_screen="home")

    if context.args:
        payload = normalize_game_payload(context.args[0])
        if payload in GAMES:
            await upsert_session(user, {"game": payload}, push_screen="game")
            await send_new_menu(
                update,
                context,
                game_details_text(payload),
                devices_keyboard(payload),
            )
            return

    await show_home_from_update(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await send_new_menu(update, context, help_text(), kb([[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.message.from_user

    def mutate(data):
        session = data.get("sessions", {}).setdefault(user_key(user.id), {})
        for key in ["game", "device", "awaiting_broadcast"]:
            session.pop(key, None)
        session["history"] = ["home"]
        session["updated_ts"] = utc_now_ts()

    await update_db(mutate)

    await send_new_menu(
        update,
        context,
        "✅ تم إلغاء الاختيار الحالي.\n\nاختر من القائمة:",
        main_menu_keyboard(user.id),
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.message.from_user
    session = get_session(user.id)

    if is_admin(user.id) and session.get("awaiting_broadcast"):
        text = update.message.text or ""
        await handle_admin_broadcast_text(update, context, text)
        return

    if await is_rate_limited(user.id, "text"):
        return

    await send_new_menu(
        update,
        context,
        "استخدم الأزرار بالأسفل لاختيار اللعبة وإتمام الطلب.\n\nإذا كنت تريد الدفع، اختر اللعبة أولًا ثم أرسل صورة الإيصال.",
        main_menu_keyboard(user.id),
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.message.from_user
    key = user_key(user.id)

    if is_admin(user.id):
        await update.message.reply_text("👑 وضع الأدمن مفعّل.\nلن يتم احتساب هذه الصورة كطلب حقيقي.")
        return

    if await is_rate_limited(user.id, "photo"):
        await update.message.reply_text("⏳ انتظر قليلًا قبل إرسال إيصال آخر.")
        return

    session = db.get("sessions", {}).get(key, {})
    game_id = session.get("game")
    device_code = session.get("device")

    if not game_id or not device_code:
        await send_new_menu(
            update,
            context,
            "⚠️ اختر اللعبة ونوع الجهاز أولًا، ثم أرسل إيصال الدفع.",
            games_keyboard(),
        )
        return

    if game_id not in GAMES or device_code not in DEVICES:
        await send_new_menu(update, context, "❌ اختيار غير صحيح. ابدأ من جديد.", games_keyboard())
        return

    existing_pending = get_pending_user_order(user.id)
    if existing_pending:
        await send_new_menu(
            update,
            context,
            "⏳ لديك طلب قيد المراجعة حاليًا.\n\n" + order_status_text(existing_pending),
            kb([
                [InlineKeyboardButton("📦 متابعة حالة الطلب", callback_data=f"receipt:status:{existing_pending.get('order_id')}")],
                [InlineKeyboardButton("📞 الدعم", callback_data=f"receipt:support:{existing_pending.get('order_id')}")],
            ]),
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
        data["stats"]["submitted_receipts"] = safe_int(data["stats"].get("submitted_receipts"), 0) + 1
        add_audit(data, "receipt_submitted", {"order_id": order_id, "user_id": user.id})

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

    await send_new_menu(
        update,
        context,
        receipt_received_text(order),
        receipt_submitted_keyboard(order_id),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data or ""
    user_id = query.from_user.id

    try:
        if data.startswith("admin:"):
            await handle_admin_callback(query, context, data, user_id)
            return

        if data.startswith("nav:"):
            await handle_nav_callback(query, context, data)
            return

        if data.startswith("receipt:"):
            await handle_receipt_callback(query, context, data)
            return

        if data.startswith("download:"):
            await handle_download_callback(query, context, data)
            return

        if data.startswith("menu:"):
            await handle_menu_callback(query, context, data)
            return

        if data.startswith("game:"):
            await handle_game_callback(query, data)
            return

        if data.startswith("device:"):
            await handle_device_callback(query, data)
            return

        if data == "pay:receipt_help":
            await edit_query_message(query, receipt_help_text(), payment_keyboard())
            return

        await query.answer("خيار غير معروف", show_alert=True)

    except Exception as error:
        logger.exception("button_handler error: %s", error)
        try:
            await query.answer("حدث خطأ أثناء معالجة الطلب", show_alert=True)
        except Exception:
            pass

async def handle_nav_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    session = get_session(query.from_user.id)
    history = session.get("history", []) or []
    previous = "home"

    if len(history) >= 2:
        previous = history[-2]

    # Remove current screen
    def mutate(data_obj):
        s = data_obj.setdefault("sessions", {}).setdefault(user_key(query.from_user.id), {})
        hist = s.get("history", []) or []
        if len(hist) > 1:
            hist.pop()
        s["history"] = hist
        s["updated_ts"] = utc_now_ts()

    await update_db(mutate)
    await show_screen(query, previous, push=False)

async def handle_menu_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    screen = data.split(":", 1)[1]

    if screen == "home":
        # الرئيسية تبدأ مسارًا جديدًا حتى لا يرجع المستخدم إلى صفحات قديمة جدًا.
        def reset_history(data_obj):
            session = data_obj.setdefault("sessions", {}).setdefault(user_key(query.from_user.id), {})
            session["history"] = ["home"]
            session["updated_ts"] = utc_now_ts()
        await update_db(reset_history)
        await show_screen(query, "home", push=False)
        return

    await show_screen(query, screen, push=True)

async def show_screen(query, screen: str, push: bool = True):
    user = query.from_user

    if push:
        await upsert_session(user, {}, push_screen=screen)

    if screen == "home":
        await show_home_from_query(query)
        return

    if screen == "games":
        await edit_query_message(query, games_text(), games_keyboard())
        return

    if screen == "payment":
        session = get_session(query.from_user.id)
        game_id = session.get("game")
        device_code = session.get("device")
        await edit_query_message(
            query,
            payment_text(get_game_title(game_id) if game_id else None, get_device_title(device_code) if device_code else None),
            payment_keyboard(),
        )
        return

    if screen == "status":
        latest = get_user_latest_order(query.from_user.id)
        if latest:
            await edit_query_message(query, order_status_text(latest), kb([
                [InlineKeyboardButton("🔄 تحديث الحالة", callback_data="menu:status")],
                [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")],
            ]))
        else:
            await edit_query_message(query, no_order_text(), kb([
                [InlineKeyboardButton("🎮 اختر لعبة", callback_data="menu:games")],
                [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")],
            ]))
        return

    if screen == "my_games":
        await edit_query_message(query, my_games_text(query.from_user.id), kb([
            [InlineKeyboardButton("🎮 طلب لعبة جديدة", callback_data="menu:games")],
            [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")],
        ]))
        return

    if screen == "support":
        await edit_query_message(query, support_text(query.from_user.id), support_keyboard())
        return

    if screen == "help":
        await edit_query_message(query, help_text(), kb([
            [InlineKeyboardButton("🎮 ابدأ الطلب", callback_data="menu:games")],
            [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")],
        ]))
        return

    if screen == "install_help":
        session = get_session(query.from_user.id)
        await edit_query_message(query, install_instructions_text(session.get("device")), kb([
            [InlineKeyboardButton("📞 الدعم", callback_data="menu:support")],
            [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")],
        ]))
        return

    await show_home_from_query(query)

async def handle_receipt_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    # أزرار خاصة برسالة الإيصال حتى لا تعتمد على history ولا ترجع للرئيسية خطأ.
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    order_id = parts[2] if len(parts) > 2 else ""

    order = db.get("orders", {}).get(order_id) or db.get("pending_payments", {}).get(order_id)
    if not order:
        await query.answer("لم أجد بيانات هذا الطلب", show_alert=True)
        return

    # لا نسمح لشخص آخر بفتح تفاصيل طلب غيره من زر قديم.
    if str(order.get("user_id")) != str(query.from_user.id) and not is_admin(query.from_user.id):
        await query.answer("هذا الطلب لا يخص حسابك", show_alert=True)
        return

    if action == "back":
        await edit_query_message(query, receipt_received_text(order), receipt_submitted_keyboard(order_id))
        return

    if action == "status":
        await edit_query_message(query, order_status_text(order), receipt_back_keyboard(order_id))
        return

    if action == "support":
        await edit_query_message(query, support_text(query.from_user.id), order_support_keyboard(order_id))
        return

    await query.answer("خيار غير معروف", show_alert=True)

async def handle_download_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    # أزرار خاصة برسالة التحميل فقط. لا تعتمد على history حتى لا يضيع زر التحميل.
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    order_id = parts[2] if len(parts) > 2 else ""

    order = db.get("orders", {}).get(order_id)
    if not order:
        await query.answer("لم أجد بيانات هذا الطلب", show_alert=True)
        return

    if str(order.get("user_id")) != str(query.from_user.id) and not is_admin(query.from_user.id):
        await query.answer("هذا الطلب لا يخص حسابك", show_alert=True)
        return

    download_url = order.get("download_url")
    if action == "back":
        if download_url:
            await edit_query_message(query, download_ready_text(order), download_keyboard(download_url, order_id))
        else:
            await edit_query_message(query, order_status_text(order), kb([
                [InlineKeyboardButton("📞 الدعم", callback_data=f"download:support:{order_id}")],
                [InlineKeyboardButton("⬅️ رجوع لحالة الطلب", callback_data=f"download:status:{order_id}")],
            ]))
        return

    if action == "install":
        await edit_query_message(query, install_instructions_text(order.get("device")), kb([
            [InlineKeyboardButton("⬅️ رجوع لزر التحميل", callback_data=f"download:back:{order_id}")],
            [InlineKeyboardButton("📞 الدعم", callback_data=f"download:support:{order_id}")],
        ]))
        return

    if action == "status":
        await edit_query_message(query, order_status_text(order), download_back_keyboard(order_id))
        return

    if action == "support":
        await edit_query_message(query, support_text(query.from_user.id), download_support_keyboard(order_id))
        return

    await query.answer("خيار غير معروف", show_alert=True)

async def handle_game_callback(query, data: str):
    game_id = data.split(":", 1)[1]
    if game_id not in GAMES:
        await query.answer("هذه اللعبة غير متوفرة حاليًا", show_alert=True)
        return

    await upsert_session(query.from_user, {"game": game_id, "device": None}, push_screen="game")
    await edit_query_message(query, game_details_text(game_id), devices_keyboard(game_id))

async def handle_device_callback(query, data: str):
    _, game_id, device_code = data.split(":")
    if game_id not in GAMES or device_code not in DEVICES:
        await query.answer("اختيار غير صحيح", show_alert=True)
        return

    await upsert_session(query.from_user, {"game": game_id, "device": device_code}, push_screen="payment")
    await edit_query_message(
        query,
        payment_text(GAMES[game_id]["title"], DEVICES[device_code]),
        payment_keyboard(),
    )

async def handle_admin_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str, user_id: int):
    if not is_admin(user_id):
        await query.answer("غير مسموح", show_alert=True)
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "panel":
        await edit_query_message(query, "👑 <b>لوحة الأدمن</b>\n\nاختر الإجراء المطلوب:", admin_panel_keyboard())
        return

    if action == "stats":
        await edit_query_message(query, admin_stats_text(), admin_panel_keyboard())
        return

    if action == "pending":
        await edit_query_message(query, admin_pending_text(), admin_panel_keyboard())
        return

    if action == "orders":
        await edit_query_message(query, admin_orders_text(), admin_panel_keyboard())
        return

    if action == "reset_confirm":
        await edit_query_message(
            query,
            "⚠️ <b>تأكيد تصفير العدادات</b>\n\nسيتم تصفير الإحصائيات فقط، ولن يتم حذف الطلبات.\nرقم الطلب التالي سيبقى محفوظًا.",
            confirm_reset_keyboard(),
        )
        return

    if action == "reset_stats":
        await reset_stats_only()
        await edit_query_message(query, "✅ تم تصفير عدادات البوت بنجاح.\n\n" + admin_stats_text(), admin_panel_keyboard())
        return

    if action == "clear_sessions_confirm":
        await edit_query_message(
            query,
            "⚠️ <b>تأكيد حذف الجلسات المؤقتة</b>\n\nهذا لا يحذف الطلبات، فقط يمسح اختيارات المستخدمين المؤقتة.",
            confirm_clear_sessions_keyboard(),
        )
        return

    if action == "clear_sessions":
        await clear_sessions_only()
        await edit_query_message(query, "✅ تم حذف الجلسات المؤقتة.", admin_panel_keyboard())
        return

    if action == "export":
        await export_db_to_admin(query, context)
        return

    if action == "broadcast_prompt":
        await upsert_session(query.from_user, {"awaiting_broadcast": True}, push_screen="admin_broadcast")
        await edit_query_message(
            query,
            "📢 <b>إرسال تنبيه للمستخدمين</b>\n\nأرسل الآن نص الرسالة في المحادثة.\n\nللإلغاء اكتب /cancel",
            kb([[InlineKeyboardButton("❌ إلغاء", callback_data="admin:panel")]]),
        )
        return

    # Order actions
    order_id = parts[2] if len(parts) > 2 else ""
    if not order_id:
        await query.answer("طلب غير معروف", show_alert=True)
        return

    order = db.get("pending_payments", {}).get(order_id)
    if not order:
        await query.answer("تمت معالجة الطلب أو غير موجود", show_alert=True)
        try:
            await query.edit_message_caption("⚠️ الطلب غير موجود أو تمت معالجته سابقًا.")
        except Exception:
            pass
        return

    if action == "info":
        await query.answer(
            "Order: " + order_id + "\n"
            "User: " + mask_user_id(order.get("user_id")) + "\n"
            "Game: " + get_game_title(order.get("game")) + "\n"
            "Device: " + get_device_title(order.get("device")),
            show_alert=True,
        )
        return

    if action == "reject_menu":
        await query.edit_message_caption(
            order_caption(order) + "\n\nاختر سبب الرفض:",
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
        await reject_order(query, context, order_id, reason_key)
        return

    if action == "approve":
        await approve_order(query, context, order_id)
        return

async def reset_stats_only() -> None:
    def mutate(data):
        order_counter = safe_int(data.get("stats", {}).get("order_counter"), 1000)
        data["stats"] = {
            "started_users": 0,
            "submitted_receipts": 0,
            "approved_orders": 0,
            "rejected_orders": 0,
            "generated_links": 0,
            "link_failures": 0,
            "order_counter": order_counter,
        }
        data["users"] = {}
        add_audit(data, "stats_reset", {"by": ADMIN_CHAT_ID})

    await update_db(mutate)

async def clear_sessions_only() -> None:
    def mutate(data):
        data["sessions"] = {}
        data["rate_limits"] = {}
        add_audit(data, "sessions_cleared", {"by": ADMIN_CHAT_ID})

    await update_db(mutate)

async def export_db_to_admin(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    export_path = DATA_DIR / f"playzone_export_{int(time.time())}.json"

    try:
        with export_path.open("w", encoding="utf-8") as file:
            json.dump(db, file, ensure_ascii=False, indent=2)

        await context.bot.send_document(
            chat_id=ADMIN_CHAT_ID,
            document=export_path.open("rb"),
            filename=export_path.name,
            caption="📤 نسخة احتياطية من بيانات PlayZone Bot",
        )
        await query.answer("تم إرسال ملف التصدير", show_alert=True)
    except Exception as error:
        logger.exception("Export failed: %s", error)
        await query.answer("فشل تصدير البيانات", show_alert=True)
    finally:
        try:
            export_path.unlink(missing_ok=True)
        except Exception:
            pass

async def handle_admin_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not update.message or not is_admin(update.message.from_user.id):
        return

    users = list(db.get("users", {}).keys())
    sent = 0
    failed = 0

    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    def mutate(data):
        session = data.setdefault("sessions", {}).setdefault(user_key(ADMIN_CHAT_ID), {})
        session.pop("awaiting_broadcast", None)
        add_audit(data, "broadcast", {"sent": sent, "failed": failed})

    await update_db(mutate)

    await update.message.reply_text(
        f"📢 تم إرسال التنبيه.\n\n✅ وصل: {sent}\n⚠️ فشل: {failed}",
        reply_markup=admin_panel_keyboard(),
    )

async def reject_order(query, context: ContextTypes.DEFAULT_TYPE, order_id: str, reason_key: str) -> None:
    reason_text = REJECTION_REASONS.get(reason_key, "تم رفض الإيصال")
    order = db.get("pending_payments", {}).get(order_id)
    if not order:
        await query.answer("الطلب غير موجود", show_alert=True)
        return

    target_user_id = int(order["user_id"])

    def mutate(data):
        current = data["pending_payments"].pop(order_id, None)
        if current:
            current["status"] = "rejected"
            current["reviewed_at"] = iso_now()
            current["rejection_reason"] = reason_text
            data["orders"][order_id] = current
        data["stats"]["rejected_orders"] = safe_int(data["stats"].get("rejected_orders"), 0) + 1
        add_audit(data, "order_rejected", {"order_id": order_id, "reason": reason_key})

    await update_db(mutate)

    await context.bot.send_message(
        chat_id=target_user_id,
        text=(
            "❌ <b>تم رفض إيصال الدفع</b>\n\n"
            "🧾 رقم الطلب: <code>" + escape_text(order_id) + "</code>\n"
            "السبب: " + escape_text(reason_text) + "\n\n"
            "يمكنك إرسال إيصال جديد أو التواصل مع الدعم."
        ),
        parse_mode="HTML",
        reply_markup=support_keyboard(),
    )

    order["status"] = "rejected"
    order["rejection_reason"] = reason_text

    try:
        await query.edit_message_caption(
            order_caption(order) + "\n\n🚫 تم الرفض في " + now_text(),
            parse_mode="HTML",
        )
    except Exception:
        pass

async def approve_order(query, context: ContextTypes.DEFAULT_TYPE, order_id: str) -> None:
    order = db.get("pending_payments", {}).get(order_id)
    if not order:
        await query.answer("الطلب غير موجود", show_alert=True)
        return

    target_user_id = int(order["user_id"])
    game_id = order["game"]
    device_code = order["device"]
    game_title = get_game_title(game_id)
    device_title = get_device_title(device_code)

    await context.bot.send_message(
        chat_id=target_user_id,
        text="✅ تم قبول الدفع.\n\nجاري تجهيز زر التحميل المؤقت الخاص بك...",
    )

    try:
        download_url = await generate_download_link(target_user_id, game_id, device_code, order_id)

        if not download_url:
            await mark_link_failed(order_id)
            await context.bot.send_message(
                chat_id=target_user_id,
                text="⚠️ تم قبول الدفع، لكن حدث خطأ أثناء تجهيز رابط التحميل.\nسيتم حل المشكلة قريبًا.",
                reply_markup=support_keyboard(),
            )
            await query.answer("فشل توليد الرابط", show_alert=True)
            return

        await context.bot.send_message(
            chat_id=target_user_id,
            text=(
                "✅ <b>طلبك جاهز</b>\n\n"
                "🧾 رقم الطلب: <code>" + escape_text(order_id) + "</code>\n"
                "🎮 اللعبة: <b>" + escape_text(game_title) + "</b>\n"
                "📱 الجهاز: <b>" + escape_text(device_title) + "</b>\n\n"
                "اضغط زر التحميل بالأسفل.\n"
                "⚠️ الرابط مؤقت وخاص بك، لا تشاركه مع أي شخص."
            ),
            parse_mode="HTML",
            reply_markup=download_keyboard(download_url, order_id),
        )

        def mutate(data):
            current = data["pending_payments"].pop(order_id, None)
            if current:
                current["status"] = "approved"
                current["reviewed_at"] = iso_now()
                current["download_url_generated"] = True
                current["download_url"] = download_url
                data["orders"][order_id] = current

            session = data.setdefault("sessions", {}).setdefault(user_key(target_user_id), {})
            session.pop("game", None)
            session.pop("device", None)
            session["history"] = ["home"]
            session["updated_ts"] = utc_now_ts()
            data["stats"]["approved_orders"] = safe_int(data["stats"].get("approved_orders"), 0) + 1
            data["stats"]["generated_links"] = safe_int(data["stats"].get("generated_links"), 0) + 1
            add_audit(data, "order_approved", {"order_id": order_id, "user_id": target_user_id})

        await update_db(mutate)

        order["status"] = "approved"

        try:
            await query.edit_message_caption(
                order_caption(order) + "\n\n✅ تم القبول وإرسال زر التحميل في " + now_text(),
                parse_mode="HTML",
            )
        except Exception:
            pass

    except Exception as error:
        logger.exception("Approve failed for order %s: %s", order_id, error)
        await mark_link_failed(order_id)
        await context.bot.send_message(
            chat_id=target_user_id,
            text="⚠️ تم قبول الدفع، لكن حدث خطأ أثناء تجهيز زر التحميل.\nسيتم حل المشكلة قريبًا.",
            reply_markup=support_keyboard(),
        )
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                "⚠️ <b>خطأ أثناء توليد الرابط</b>\n\n"
                "🧾 الطلب: <code>" + escape_text(order_id) + "</code>\n"
                "الخطأ: " + escape_text(str(error))
            ),
            parse_mode="HTML",
        )

async def mark_link_failed(order_id: str) -> None:
    def mutate(data):
        current = data["pending_payments"].get(order_id)
        if current:
            current["status"] = "link_failed"
            data["orders"][order_id] = current.copy()
        data["stats"]["link_failures"] = safe_int(data["stats"].get("link_failures"), 0) + 1
        add_audit(data, "link_failed", {"order_id": order_id})

    await update_db(mutate)

# =====================================================
# Admin Commands
# =====================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update.message.from_user.id):
        return
    await send_new_menu(update, context, "👑 <b>لوحة الأدمن</b>\n\nاختر الإجراء:", admin_panel_keyboard())

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update.message.from_user.id):
        return
    await cleanup_expired_data()
    await send_new_menu(update, context, admin_stats_text(), admin_panel_keyboard())

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update.message.from_user.id):
        return
    await send_new_menu(update, context, admin_pending_text(), admin_panel_keyboard())

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update.message.from_user.id):
        return
    await send_new_menu(update, context, admin_orders_text(), admin_panel_keyboard())

async def reset_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update.message.from_user.id):
        return
    await reset_stats_only()
    await send_new_menu(update, context, "✅ تم تصفير عدادات البوت.\n\n" + admin_stats_text(), admin_panel_keyboard())

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
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))

    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("pending", admin_pending))
    application.add_handler(CommandHandler("orders", admin_orders))
    application.add_handler(CommandHandler("reset_stats", reset_stats_command))

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

# redeploy trigger ultra professional