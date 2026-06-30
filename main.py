import os
import sys
import json
import time
import hmac
import html
import signal
import asyncio
import logging
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
# PlayZone Telegram Bot - Ultra Professional V2.5
# (CRM, Anti-Share, Dynamic Games, Vouchers, Auto-Backup)
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("playzone-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID", "8569699093").strip()
DOWNLOAD_API_URL = os.getenv("DOWNLOAD_API_URL", "https://gfdbgta.pythonanywhere.com/generate_link").strip()
DOWNLOAD_API_SECRET = os.getenv("DOWNLOAD_API_SECRET", "").strip()
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://instagram.com/p1ay.zone").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DB_FILE = DATA_DIR / "playzone_bot_data.json"

PAYMENT_TIMEOUT_SECONDS = int(os.getenv("PAYMENT_TIMEOUT_SECONDS", str(60 * 60 * 24)))
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", str(60 * 60 * 2)))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "3"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
except ValueError as exc:
    raise RuntimeError("ADMIN_CHAT_ID must be an integer") from exc

def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_CHAT_ID)

# =====================================================
# Web Server (Keep Alive)
# =====================================================
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ PlayZone Bot V2.5 is alive and running!"

def run_web_server():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    thread = Thread(target=run_web_server, daemon=True)
    thread.start()

# =====================================================
# Advanced Database Structure
# =====================================================
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
    "link_failed": "⚠️ فشل التوليد",
}

REJECTION_REASONS = {
    "unclear": "الإيصال غير واضح",
    "wrong_amount": "المبلغ غير صحيح",
    "not_received": "لم يصل التحويل",
    "invalid_image": "صورة غير صالحة",
}

DEFAULT_DB = {
    "settings": {
        "game_price": "1000 IQD",
        "payment_card": "7113282938",
        "maintenance_mode": False,
    },
    "games": {
        "thechallenge": {
            "title": "The Challenge",
            "emoji": "🎮",
            "description": "مغامرة مليئة بالتحديات والألغاز الشيقة.",
            "available_devices": ["android", "ios"],
        }
    },
    "banned_users": [],
    "vouchers": {
        "FREE-PLAYZONE": {"game_id": "thechallenge", "is_active": True, "uses_left": 5}
    },
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
# Core Database Helpers
# =====================================================
def utc_now_ts() -> int: return int(time.time())
def iso_now() -> str: return datetime.now(timezone.utc).isoformat()
def now_text() -> str: return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def user_key(user_id: int) -> str: return str(user_id)
def escape_text(value: Any) -> str: return html.escape(str(value or ""))

def get_setting(key: str) -> Any:
    return db.get("settings", {}).get(key, DEFAULT_DB["settings"].get(key))

def get_game_title(game_id: str) -> str:
    return db.get("games", {}).get(game_id, {}).get("title", game_id or "غير معروف")

def safe_int(value: Any, default: int = 0) -> int:
    try: return int(value)
    except Exception: return default

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
    try:
        if DB_FILE.exists(): DB_FILE.replace(DB_FILE.with_suffix(".bak"))
    except Exception: pass
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
        expired_payments = [k for k, p in data["pending_payments"].items() if now - safe_int(p.get("created_ts"), now) > PAYMENT_TIMEOUT_SECONDS]
        for k in expired_payments:
            p = data["pending_payments"].pop(k, None)
            if p:
                p["status"] = "expired"
                data["orders"][k] = p
        expired_sessions = [k for k, s in data["sessions"].items() if now - safe_int(s.get("updated_ts", s.get("created_ts", now)), now) > SESSION_TIMEOUT_SECONDS]
        for k in expired_sessions:
            data["sessions"].pop(k, None)
        old_limits = [k for k, ts in data["rate_limits"].items() if now - safe_int(ts, now) > RATE_LIMIT_SECONDS * 10]
        for k in old_limits:
            data["rate_limits"].pop(k, None)
    await update_db(mutate)

async def is_rate_limited(user_id: int, action: str) -> bool:
    if is_admin(user_id): return False
    now = utc_now_ts()
    key = f"{user_id}:{action}"
    def mutate(data):
        last = safe_int(data["rate_limits"].get(key), 0)
        if now - last < RATE_LIMIT_SECONDS: return True
        data["rate_limits"][key] = now
        return False
    return await update_db(mutate)

async def next_order_id() -> str:
    def mutate(data):
        current = safe_int(data["stats"].get("order_counter"), 1000) + 1
        data["stats"]["order_counter"] = current
        return "PZ-" + str(current)
    return await update_db(mutate)

def get_session(user_id: int) -> Dict[str, Any]:
    return db.get("sessions", {}).get(user_key(user_id), {})

async def upsert_session(user, changes: Dict[str, Any], push_screen: Optional[str] = None) -> Dict[str, Any]:
    key = user_key(user.id)
    def mutate(data):
        session = data.setdefault("sessions", {}).setdefault(key, {"user_id": user.id, "history": []})
        session.update({"full_name": user.full_name, "username": user.username or "", "updated_ts": utc_now_ts()})
        session.update(changes)
        if push_screen:
            history = session.setdefault("history", [])
            if not history or history[-1] != push_screen:
                history.append(push_screen)
                session["history"] = history[-8:]
        return session.copy()
    return await update_db(mutate)

def get_user_orders(user_id: int) -> List[Dict[str, Any]]:
    orders = [o for o in db.get("orders", {}).values() if str(o.get("user_id")) == str(user_id)]
    orders.sort(key=lambda x: safe_int(x.get("created_ts"), 0), reverse=True)
    return orders

def is_user_banned(user_id: int) -> bool:
    return str(user_id) in db.get("banned_users", [])

# =====================================================
# Keyboards & UI
# =====================================================
def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup: return InlineKeyboardMarkup(rows)

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
            InlineKeyboardButton("📞 الدعم الفني", callback_data="menu:support"),
        ],
        [InlineKeyboardButton("🎁 استرداد كود هدية", callback_data="menu:redeem_voucher")]
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("👑 لوحة تحكم الإدارة", callback_data="admin:panel")])
    return kb(rows)

def games_keyboard() -> InlineKeyboardMarkup:
    rows = []
    games = db.get("games", {})
    for game_id, game in games.items():
        rows.append([InlineKeyboardButton(f"{game['emoji']} {game['title']}", callback_data=f"game:{game_id}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")])
    return kb(rows)

def devices_keyboard(game_id: str) -> InlineKeyboardMarkup:
    rows = []
    game = db.get("games", {}).get(game_id, {})
    for device_code in game.get("available_devices", []):
        rows.append([InlineKeyboardButton(DEVICES.get(device_code, device_code), callback_data=f"device:{game_id}:{device_code}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")])
    return kb(rows)

def support_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("✉️ إرسال تذكرة للإدارة (مباشر)", callback_data="menu:contact_admin")],
        [InlineKeyboardButton("📞 تواصل عبر إنستغرام", url=SUPPORT_URL)],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")],
    ])

def payment_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("📸 أرسلت الإيصال", callback_data="pay:receipt_help")],
        [InlineKeyboardButton("🎮 تغيير اللعبة", callback_data="menu:games")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")],
    ])

# ================== Admin UI ==================
def admin_panel_keyboard() -> InlineKeyboardMarkup:
    is_maint = "🔴 إيقاف الصيانة" if get_setting("maintenance_mode") else "🟢 تشغيل الصيانة"
    return kb([
        [
            InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats"),
            InlineKeyboardButton("⏳ الطلبات المعلقة", callback_data="admin:pending"),
        ],
        [
            InlineKeyboardButton("🎮 إدارة الألعاب", callback_data="admin:manage_games"),
            InlineKeyboardButton("📢 الإذاعة", callback_data="admin:smart_broadcast"),
        ],
        [
            InlineKeyboardButton("⚙️ إعدادات البوت", callback_data="admin:settings"),
            InlineKeyboardButton(is_maint, callback_data="admin:toggle_maintenance"),
        ],
        [
            InlineKeyboardButton("📤 أخذ نسخة احتياطية", callback_data="admin:export"),
        ],
        [InlineKeyboardButton("🏠 إغلاق اللوحة", callback_data="menu:home")],
    ])

def manage_games_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("➕ إضافة لعبة جديدة", callback_data="admin:add_game_start")],
        [InlineKeyboardButton("🗑️ حذف لعبة", callback_data="admin:delete_game_list")],
        [InlineKeyboardButton("⬅️ رجوع للوحة", callback_data="admin:panel")]
    ])

def delete_games_keyboard() -> InlineKeyboardMarkup:
    rows = []
    games = db.get("games", {})
    for game_id, game in games.items():
        rows.append([InlineKeyboardButton(f"❌ حذف {game['title']}", callback_data=f"admin:del_game:{game_id}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="admin:manage_games")])
    return kb(rows)

def admin_review_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("✅ قبول (تجهيز زر التحميل)", callback_data=f"admin:approve:{order_id}")],
        [
            InlineKeyboardButton("❌ رفض الإيصال", callback_data=f"admin:reject_menu:{order_id}"),
            InlineKeyboardButton("ℹ️ معلومات العميل", callback_data=f"admin:info:{order_id}"),
        ],
        [InlineKeyboardButton("👑 العودة للوحة", callback_data="admin:panel")],
    ])

def rejection_reasons_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("❌ الإيصال غير واضح", callback_data=f"admin:reject_reason:{order_id}:unclear")],
        [InlineKeyboardButton("❌ المبلغ غير صحيح", callback_data=f"admin:reject_reason:{order_id}:wrong_amount")],
        [InlineKeyboardButton("❌ لم يصل التحويل", callback_data=f"admin:reject_reason:{order_id}:not_received")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data=f"admin:back:{order_id}")],
    ])

# =====================================================
# Anti-Share Protected Download UI
# =====================================================
def protected_download_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("⬇️ اضغط لتوليد الرابط الآمن الخاص بك", callback_data=f"download:reveal:{order_id}")],
        [
            InlineKeyboardButton("📲 طريقة التثبيت", callback_data=f"download:install:{order_id}"),
            InlineKeyboardButton("📞 الدعم الفني", callback_data=f"download:support:{order_id}")
        ]
    ])

def actual_download_keyboard(download_url: str, order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("✅ تحميل اللعبة الآن", url=download_url)],
        [InlineKeyboardButton("📲 طريقة التثبيت", callback_data=f"download:install:{order_id}")]
    ])

# =====================================================
# External API / Secure Link Generation
# =====================================================
def sign_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not DOWNLOAD_API_SECRET: return payload
    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["signature"] = hmac.new(DOWNLOAD_API_SECRET.encode("utf-8"), message, "sha256").hexdigest()
    return payload

async def generate_download_link(user_id: int, game_id: str, device_code: str, order_id: str) -> Optional[str]:
    payload = sign_payload({
        "user_id": str(user_id),
        "device": device_code,
        "game": game_id.lower(),
        "order_id": order_id,
        "timestamp": utc_now_ts(),
    })
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(DOWNLOAD_API_URL, json=payload) as response:
                if response.status >= 400: return None
                data = await response.json()
                return data.get("download_url")
    except Exception as e:
        logger.error(f"API Error: {e}")
        return None

# =====================================================
# Background Jobs
# =====================================================
async def auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    if not DB_FILE.exists(): return
    try:
        await context.bot.send_document(
            chat_id=ADMIN_CHAT_ID,
            document=DB_FILE.open("rb"),
            filename=f"PlayZone_Backup_{iso_now()[:10]}.json",
            caption="🔄 نسخة احتياطية تلقائية لقاعدة البيانات (Auto-Backup)."
        )
    except Exception as e:
        logger.error(f"Auto-backup failed: {e}")

# =====================================================
# Message Handlers
# =====================================================
async def check_maintenance(update: Update) -> bool:
    user_id = update.effective_user.id
    if get_setting("maintenance_mode") and not is_admin(user_id):
        if update.message:
            await update.message.reply_text("🛠️ <b>عذراً!</b>\nالبوت حالياً في وضع الصيانة لإضافة تحديثات وألعاب جديدة. نعود لكم بعد قليل ⏳", parse_mode="HTML")
        elif update.callback_query:
            await update.callback_query.answer("🛠️ البوت في وضع الصيانة حالياً.", show_alert=True)
        return True
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_maintenance(update): return
    if is_user_banned(update.effective_user.id): return

    await cleanup_expired_data()
    user = update.effective_user
    
    key = user_key(user.id)
    def reg(data):
        if key not in data.setdefault("users", {}):
            data["users"][key] = {"user_id": user.id, "first_seen": iso_now()}
            data["stats"]["started_users"] = safe_int(data["stats"].get("started_users"), 0) + 1
    await update_db(reg)
    await upsert_session(user, {}, push_screen="home")

    text = f"👋 <b>أهلاً بك في PlayZone</b>\n\nمتجرك الأول والآمن لتحميل الألعاب.\nاختر من القائمة بالأسفل لتبدأ:"
    if is_admin(user.id): text += "\n\n👑 <b>أهلاً بك أيها المدير. وضع الأدمن مفعل.</b>"
    
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(user.id))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_maintenance(update): return
    if is_user_banned(update.effective_user.id): return
    
    user = update.message.from_user
    text = update.message.text.strip()
    session = get_session(user.id)

    # 1. Admin Adding a New Game
    step = session.get("adding_game_step")
    if is_admin(user.id) and step:
        game_data = session.get("new_game_data", {})
        
        if step == "id":
            game_id = text.lower().strip().replace(" ", "")
            game_data["id"] = game_id
            await upsert_session(user, {"adding_game_step": "title", "new_game_data": game_data})
            await update.message.reply_text("✅ ممتاز.\nالآن أرسل <b>اسم اللعبة</b> كما سيظهر للمستخدمين:", parse_mode="HTML")
            return
            
        elif step == "title":
            game_data["title"] = text
            await upsert_session(user, {"adding_game_step": "emoji", "new_game_data": game_data})
            await update.message.reply_text("✅ رائع.\nالآن أرسل <b>إيموجي</b> يعبر عن اللعبة (مثال: 🚗):", parse_mode="HTML")
            return
            
        elif step == "emoji":
            game_data["emoji"] = text
            await upsert_session(user, {"adding_game_step": "desc", "new_game_data": game_data})
            await update.message.reply_text("✅ تم.\nالآن أرسل <b>وصف اللعبة</b>:", parse_mode="HTML")
            return
            
        elif step == "desc":
            game_data["description"] = text
            await upsert_session(user, {"adding_game_step": "devices", "new_game_data": game_data})
            await update.message.reply_text("✅ خطوة أخيرة.\nاكتب الأجهزة المدعومة (مثال: <code>android, ios</code>):", parse_mode="HTML")
            return
            
        elif step == "devices":
            devices = [d.strip() for d in text.lower().split(",") if d.strip() in ["android", "ios"]]
            if not devices: devices = ["android"]
            
            game_data["available_devices"] = devices
            game_id = game_data.pop("id")
            
            def save_new_game(data):
                data.setdefault("games", {})[game_id] = game_data
            await update_db(save_new_game)
            await upsert_session(user, {"adding_game_step": None, "new_game_data": None})
            
            await update.message.reply_text(
                f"🎉 <b>تمت إضافة اللعبة بنجاح!</b>\n\n🎮 {game_data['emoji']} {game_data['title']}\n📱 الأجهزة: {', '.join(devices)}\n\nوهي متاحة الآن للجميع.",
                parse_mode="HTML",
                reply_markup=admin_panel_keyboard()
            )
            return

    # 2. Admin Replying to a User Ticket
    if is_admin(user.id) and session.get("replying_to_user"):
        target = session.get("reply_target_id")
        try:
            await context.bot.send_message(chat_id=target, text=f"👨‍💻 <b>رد من الدعم الفني:</b>\n\n{escape_text(text)}", parse_mode="HTML")
            await update.message.reply_text("✅ تم إرسال الرد للعميل بنجاح.", reply_markup=admin_panel_keyboard())
        except Exception:
            await update.message.reply_text("⚠️ فشل الإرسال، العميل قام بحظر البوت.")
        await upsert_session(user, {"replying_to_user": False, "reply_target_id": None})
        return

    # 3. Admin Smart Broadcast
    if is_admin(user.id) and session.get("awaiting_broadcast"):
        target_group = session.get("broadcast_target", "all")
        users_dict = db.get("users", {})
        count, failed = 0, 0
        
        for uid_str in users_dict.keys():
            if target_group == "buyers" and len(get_user_orders(int(uid_str))) == 0: continue
            if target_group == "no_buyers" and len(get_user_orders(int(uid_str))) > 0: continue
            try:
                await context.bot.send_message(chat_id=int(uid_str), text=f"📢 <b>إعلان:</b>\n\n{text}", parse_mode="HTML")
                count += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
                
        await upsert_session(user, {"awaiting_broadcast": False})
        await update.message.reply_text(f"✅ انتهت الإذاعة!\nوصلت لـ {count} مستخدم.\nفشلت لـ {failed} مستخدم.", reply_markup=admin_panel_keyboard())
        return

    # 4. User Sending a Support Ticket
    if session.get("awaiting_support_msg"):
        order_count = len(get_user_orders(user.id))
        alert = (
            f"📩 <b>تذكرة دعم جديدة</b>\n"
            f"👤 من: {escape_text(user.full_name)} | <code>{user.id}</code>\n"
            f"🛍️ إجمالي طلباته السابقة: {order_count}\n\n"
            f"💬 الرسالة:\n{escape_text(text)}"
        )
        kb_reply = kb([[InlineKeyboardButton("📝 رد مباشر", callback_data=f"admin:reply_ticket:{user.id}")]])
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=alert, parse_mode="HTML", reply_markup=kb_reply)
        await upsert_session(user, {"awaiting_support_msg": False})
        await update.message.reply_text("✅ تم إرسال رسالتك لفريق الدعم. سيصلك الرد هنا قريباً.", reply_markup=main_menu_keyboard(user.id))
        return

    # 5. User Redeeming a Voucher
    if session.get("awaiting_voucher"):
        code = text.upper()
        vouchers = db.get("vouchers", {})
        
        if code in vouchers and vouchers[code].get("is_active", True) and vouchers[code].get("uses_left", 0) > 0:
            game_id = vouchers[code]["game_id"]
            order_id = await next_order_id()
            
            def mutate_voucher(data):
                data["vouchers"][code]["uses_left"] -= 1
                if data["vouchers"][code]["uses_left"] <= 0:
                    data["vouchers"][code]["is_active"] = False
                order = {
                    "order_id": order_id,
                    "game": game_id,
                    "device": "android",
                    "user_id": user.id,
                    "status": "approved",
                    "price": "0 (كود هدية)",
                    "created_ts": utc_now_ts(),
                    "created_at_text": now_text(),
                }
                data["orders"][order_id] = order
            await update_db(mutate_voucher)
            
            await update.message.reply_text(f"🎉 <b>مبروك!</b> الكود صحيح وتم شحن اللعبة بحسابك.\nاذهب إلى <b>(ألعابي)</b> من القائمة لتجد اللعبة، أو اضغط أدناه:", parse_mode="HTML", reply_markup=kb([[InlineKeyboardButton("🕹️ ألعابي", callback_data="menu:my_games")]]))
            await upsert_session(user, {"awaiting_voucher": False})
            return
        else:
            await update.message.reply_text("❌ الكود غير صحيح، منتهي الصلاحية، أو تم استخدامه مسبقاً.")
            return

    await update.message.reply_text("استخدم الأزرار للتنقل أو اتبع التعليمات على الشاشة.", reply_markup=main_menu_keyboard(user.id))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_maintenance(update): return
    if is_user_banned(update.effective_user.id): return
    
    user = update.message.from_user
    if is_admin(user.id): return
    
    if await is_rate_limited(user.id, "photo"):
        await update.message.reply_text("⏳ مهلاً، نرجو الانتظار قليلاً قبل إرسال صورة أخرى.")
        return

    session = get_session(user.id)
    game_id, device_code = session.get("game"), session.get("device")
    
    if not game_id or not device_code:
        await update.message.reply_text("⚠️ الرجاء اختيار اللعبة ونوع الجهاز من القائمة أولاً، ثم إرسال الإيصال.", reply_markup=games_keyboard())
        return

    pending_count = len([o for o in db.get("pending_payments", {}).values() if str(o["user_id"]) == str(user.id)])
    if pending_count > 0:
        await update.message.reply_text("⏳ لديك طلب قيد المراجعة حالياً. لا يمكنك إرسال إيصال جديد حتى تتم مراجعة القديم.")
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
        "username": user.username or "لا يوجد",
        "status": "pending",
        "price": get_setting("game_price"),
        "created_ts": utc_now_ts(),
        "created_at_text": now_text(),
    }

    def mutate(data):
        data["pending_payments"][order_id] = order
        data["orders"][order_id] = order.copy()
        data["stats"]["submitted_receipts"] = safe_int(data["stats"].get("submitted_receipts"), 0) + 1
    await update_db(mutate)

    caption = (
        f"📩 <b>مراجعة إيصال جديد</b>\n\n"
        f"🧾 رقم الطلب: <code>{order_id}</code>\n"
        f"👤 العميل: {escape_text(user.full_name)} | <code>{user.id}</code>\n"
        f"🎮 اللعبة: {escape_text(get_game_title(game_id))}\n"
        f"📱 الجهاز: {escape_text(get_device_title(device_code))}\n"
        f"💰 المبلغ المتوقع: {get_setting('game_price')}"
    )

    try:
        await context.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=file_id, caption=caption, parse_mode="HTML", reply_markup=admin_review_keyboard(order_id))
    except Exception as e:
        logger.error(f"Failed to forward receipt: {e}")
    
    await update.message.reply_text(
        f"✅ <b>تم استلام الإيصال بنجاح</b>\n\n🧾 رقم طلبك: <code>{order_id}</code>\nجاري مراجعة الإيصال من قبل الإدارة، سيصلك زر التحميل هنا قريباً.",
        parse_mode="HTML",
        reply_markup=kb([[InlineKeyboardButton("📦 حالة الطلب", callback_data="menu:status")]])
    )

# =====================================================
# Callback Queries Routing
# =====================================================
async def edit_msg(query, text: str, markup: InlineKeyboardMarkup):
    try: await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        try: await query.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception: pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if await check_maintenance(update): return
    user_id = query.from_user.id
    if is_user_banned(user_id): return
    
    data = query.data
    parts = data.split(":")
    
    try:
        if data.startswith("menu:"):
            await handle_menu(query, parts[1])
        elif data.startswith("nav:"):
            await handle_nav(query)
        elif data.startswith("game:"):
            game_id = parts[1]
            await upsert_session(query.from_user, {"game": game_id}, push_screen="game")
            game_info = db.get("games", {}).get(game_id, {})
            text = f"<b>{escape_text(get_game_title(game_id))}</b>\n\n{escape_text(game_info.get('description', ''))}\n\nاختر نوع جهازك:"
            await edit_msg(query, text, devices_keyboard(game_id))
        elif data.startswith("device:"):
            game_id, dev = parts[1], parts[2]
            await upsert_session(query.from_user, {"game": game_id, "device": dev}, push_screen="payment")
            price = get_setting("game_price")
            card = get_setting("payment_card")
            text = f"💳 <b>إتمام الدفع</b>\n\n🎮 اللعبة: {escape_text(get_game_title(game_id))}\n📱 الجهاز: {DEVICES.get(dev)}\n💰 السعر: <b>{price}</b>\n\nقم بتحويل المبلغ لبطاقة الماستر كارد:\n<code>{card}</code>\n\n📸 <b>ثم أرسل صورة الإيصال هنا في هذه المحادثة.</b>"
            await edit_msg(query, text, payment_keyboard())
            
        elif data.startswith("download:"):
            await handle_download(query, context, parts)
            
        elif data.startswith("admin:"):
            await handle_admin(query, context, parts)
            
        elif data == "pay:receipt_help":
            await edit_msg(query, "📸 <b>طريقة الإرسال:</b>\n\nفقط قم بالتقاط صورة من تطبيق زين كاش أو البنك لحوالتك، واضغط زر الإرفاق (📎) وأرسلها كصورة عادية في المحادثة.", payment_keyboard())
    except Exception as e:
        logger.error(f"Callback error: {e}")

async def handle_menu(query, screen: str):
    user = query.from_user
    if screen == "home":
        await upsert_session(user, {"history": ["home"]})
        await edit_msg(query, "👋 <b>أهلاً بك في القائمة الرئيسية</b>\nاختر ما يناسبك:", main_menu_keyboard(user.id))
    elif screen == "games":
        await upsert_session(user, {}, push_screen="games")
        await edit_msg(query, "🎮 <b>الألعاب المتوفرة</b>\nاختر اللعبة:", games_keyboard())
    elif screen == "status":
        latest = get_user_orders(user.id)
        if latest:
            order = latest[0]
            status = STATUS_LABELS.get(order.get("status", "pending"))
            txt = f"📦 <b>أحدث طلب لك</b>\n\n🧾 رقم: <code>{order['order_id']}</code>\n🎮 اللعبة: {get_game_title(order['game'])}\n📌 الحالة: <b>{status}</b>"
            if order.get("status") == "approved":
                await edit_msg(query, txt, protected_download_keyboard(order["order_id"]))
            else:
                await edit_msg(query, txt, kb([[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]))
        else:
            await edit_msg(query, "لا يوجد لديك طلبات سابقة.", kb([[InlineKeyboardButton("🎮 تصفح الألعاب", callback_data="menu:games")]]))
    elif screen == "my_games":
        orders = [o for o in get_user_orders(user.id) if o.get("status") == "approved"]
        if not orders:
            await edit_msg(query, "لم تقم بشراء أي ألعاب بعد.", kb([[InlineKeyboardButton("🎮 تصفح الألعاب", callback_data="menu:games")]]))
            return
        lines = ["🕹️ <b>ألعابك المشتراة</b>\n"]
        for o in orders[:10]:
            lines.append(f"• {get_game_title(o['game'])} (<code>{o['order_id']}</code>)")
        lines.append("\nلتحميل لعبة مجدداً، اذهب لحالة الطلب أو تواصل مع الدعم.")
        await edit_msg(query, "\n".join(lines), kb([[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]))
    elif screen == "contact_admin":
        await upsert_session(user, {"awaiting_support_msg": True}, push_screen="contact_admin")
        await edit_msg(query, "✉️ <b>مراسلة الإدارة</b>\n\nاكتب استفسارك أو مشكلتك في رسالة واحدة الآن وسنقوم بالرد عليك بأقرب وقت:", kb([[InlineKeyboardButton("❌ إلغاء", callback_data="menu:home")]]))
    elif screen == "support":
        await upsert_session(user, {}, push_screen="support")
        await edit_msg(query, "📞 <b>مركز الدعم الفني</b>\nنحن هنا لمساعدتك:", support_keyboard())
    elif screen == "redeem_voucher":
        await upsert_session(user, {"awaiting_voucher": True}, push_screen="redeem_voucher")
        await edit_msg(query, "🎁 <b>استرداد كود هدية</b>\n\nأرسل الكود الآن في المحادثة لفتح اللعبة مجاناً:", kb([[InlineKeyboardButton("❌ إلغاء", callback_data="menu:home")]]))

async def handle_nav(query):
    session = get_session(query.from_user.id)
    history = session.get("history", []) or ["home"]
    if len(history) > 1: history.pop()
    await update_db(lambda d: d.setdefault("sessions", {}).setdefault(user_key(query.from_user.id), {}).update({"history": history}))
    await handle_menu(query, history[-1] if history else "home")

# =====================================================
# Anti-Share Logic
# =====================================================
async def handle_download(query, context, parts):
    action, order_id = parts[1], parts[2]
    order = db.get("orders", {}).get(order_id)
    
    if not order:
        return await query.answer("لم أجد هذا الطلب!", show_alert=True)
    
    if str(order["user_id"]) != str(query.from_user.id) and not is_admin(query.from_user.id):
        return await query.answer("❌ هذا الرابط محمي وخاص بمشتري اللعبة فقط!", show_alert=True)

    if action == "reveal":
        await query.answer("⏳ جاري توليد رابط مشفر وخاص بجهازك...", show_alert=False)
        download_url = await generate_download_link(order["user_id"], order["game"], order["device"], order_id)
        
        if not download_url:
            return await query.answer("⚠️ فشل الاتصال بخادم التحميل. الرجاء المحاولة لاحقاً.", show_alert=True)
            
        txt = (f"✅ <b>تم توليد رابطك بنجاح!</b>\n\n⚠️ <b>تحذير:</b> الرابط سيعمل لجهازك فقط وسينتهي خلال 10 دقائق.\n"
               f"لا تقم بمشاركته مع أي شخص، وإلا سيتم حظر الرابط تلقائياً.")
        await edit_msg(query, txt, actual_download_keyboard(download_url, order_id))

    elif action == "install":
        dev = order.get("device")
        txt = "📲 <b>التثبيت:</b>\n1️⃣ اضغط التحميل.\n2️⃣ افتح ملف APK.\n3️⃣ وافق على 'التثبيت من مصادر غير معروفة'." if dev == "android" else "🍎 <b>الآيفون:</b>\nتثبيت ألعاب خارج المتجر يتطلب خطوات كشهادات التطوير. راسل الدعم."
        await edit_msg(query, txt, kb([[InlineKeyboardButton("⬅️ رجوع", callback_data=f"menu:status")]]))
        
    elif action == "support":
        await edit_msg(query, "📞 <b>الدعم الفني:</b>", support_keyboard())

# =====================================================
# Admin Control Panel
# =====================================================
async def handle_admin(query, context, parts):
    if not is_admin(query.from_user.id): return
    action = parts[1]
    
    if action == "panel":
        await edit_msg(query, "👑 <b>لوحة تحكم النظام (CRM)</b>\nالتحكم الكامل بجميع العمليات:", admin_panel_keyboard())
        
    elif action == "manage_games":
        await edit_msg(query, "🎮 <b>إدارة الألعاب</b>\nيمكنك إضافة ألعاب جديدة أو حذف الألعاب الحالية:", manage_games_keyboard())

    elif action == "add_game_start":
        await upsert_session(query.from_user, {"adding_game_step": "id", "new_game_data": {}})
        await edit_msg(query, "➕ <b>إضافة لعبة جديدة (الخطوة 1 من 5)</b>\n\nأرسل الآن <b>المعرف البرمجي للعبة (ID)</b> باللغة الإنجليزية وبدون مسافات (مثال: <code>gta6</code>):\n\nللإلغاء اكتب /cancel", kb([]))
        
    elif action == "delete_game_list":
        await edit_msg(query, "🗑️ <b>حذف لعبة</b>\nاختر اللعبة التي تريد حذفها نهائياً:", delete_games_keyboard())
        
    elif action == "del_game":
        game_id = parts[2]
        await update_db(lambda d: d["games"].pop(game_id, None))
        await query.answer("✅ تم حذف اللعبة بنجاح!", show_alert=True)
        await edit_msg(query, "🗑️ <b>حذف لعبة</b>\nتم تحديث القائمة:", delete_games_keyboard())

    elif action == "toggle_maintenance":
        current = get_setting("maintenance_mode")
        await update_db(lambda d: d["settings"].update({"maintenance_mode": not current}))
        await query.answer(f"وضع الصيانة الآن: {'مفعل' if not current else 'متوقف'}", show_alert=True)
        await edit_msg(query, "👑 <b>لوحة تحكم النظام (CRM)</b>", admin_panel_keyboard())
        
    elif action == "pending":
        pending = sorted(db.get("pending_payments", {}).values(), key=lambda x: safe_int(x.get("created_ts"), 0))
        if not pending: return await edit_msg(query, "✅ لا توجد طلبات معلقة!", admin_panel_keyboard())
        o = pending[0]
        if o.get("file_id"):
            await context.bot.send_photo(ADMIN_CHAT_ID, o["file_id"], caption=f"⚠️ <b>يوجد {len(pending)} طلبات معلقة!</b>\n\nرقم: <code>{o['order_id']}</code>\nاللعبة: {get_game_title(o['game'])}", parse_mode="HTML", reply_markup=admin_review_keyboard(o["order_id"]))
            try: await query.message.delete()
            except Exception: pass
            
    elif action == "smart_broadcast":
        await edit_msg(query, "📢 <b>الإذاعة الموجهة</b>\nاختر الفئة المستهدفة:", kb([
            [InlineKeyboardButton("🌍 للجميع", callback_data="admin:broadcast:all")],
            [InlineKeyboardButton("💳 المشترين فقط", callback_data="admin:broadcast:buyers")],
            [InlineKeyboardButton("👀 المترددين (لم يشتروا)", callback_data="admin:broadcast:no_buyers")],
            [InlineKeyboardButton("⬅️ إلغاء", callback_data="admin:panel")]
        ]))
        
    elif action == "broadcast":
        target = parts[2]
        await upsert_session(query.from_user, {"awaiting_broadcast": True, "broadcast_target": target})
        await edit_msg(query, f"📢 أرسل نص الإعلان الآن (الاستهداف: {target})\n(أرسل أي رسالة نصية، وللإلغاء أرسل /cancel)", kb([]))
        
    elif action == "export":
        export_path = DATA_DIR / f"DB_Backup_{int(time.time())}.json"
        with export_path.open("w", encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False, indent=2)
        await context.bot.send_document(ADMIN_CHAT_ID, export_path.open("rb"), caption="📤 النسخة الاحتياطية الحالية.")
        await query.answer("تم التصدير", show_alert=True)
        
    # Receipt Actions
    elif action in ["approve", "reject_menu", "reject_reason", "info", "back", "reply_ticket"]:
        if action == "reply_ticket":
            target_user = parts[2]
            await upsert_session(query.from_user, {"replying_to_user": True, "reply_target_id": target_user})
            return await edit_msg(query, f"📝 <b>الرد على المستخدم <code>{target_user}</code></b>\n\nأرسل رسالتك الآن في المحادثة:\n(للإلغاء اكتب /cancel)", kb([[InlineKeyboardButton("❌ إلغاء", callback_data="admin:panel")]]))

        order_id = parts[2]
        order = db.get("pending_payments", {}).get(order_id)
        if not order and action != "info": return await query.answer("تمت معالجة الطلب مسبقاً!", show_alert=True)
            
        if action == "approve":
            await process_approval(query, context, order)
        elif action == "reject_menu":
            await query.edit_message_reply_markup(reply_markup=rejection_reasons_keyboard(order_id))
        elif action == "back":
            await query.edit_message_reply_markup(reply_markup=admin_review_keyboard(order_id))
        elif action == "reject_reason":
            await process_rejection(query, context, order, parts[3])
        elif action == "info":
            o = order or db.get("orders", {}).get(order_id)
            if o: await query.answer(f"Client: {o.get('full_name')}\nUser ID: {o.get('user_id')}", show_alert=True)

async def process_approval(query, context, order):
    order_id = order["order_id"]
    def mutate(data):
        current = data["pending_payments"].pop(order_id, None)
        if current:
            current["status"] = "approved"
            data["orders"][order_id] = current
            data["stats"]["approved_orders"] = safe_int(data["stats"].get("approved_orders"), 0) + 1
    await update_db(mutate)
    
    txt = f"✅ <b>تم قبول إيصال الدفع!</b>\n\n🎮 اللعبة: {get_game_title(order['game'])}\n🧾 الطلب: <code>{order_id}</code>\n\nاضغط لتوليد الرابط الآمن:"
    try: await context.bot.send_message(chat_id=order["user_id"], text=txt, parse_mode="HTML", reply_markup=protected_download_keyboard(order_id))
    except Exception: pass
    try: await query.edit_message_caption(caption=f"✅ تمت الموافقة على الطلب <code>{order_id}</code>.", parse_mode="HTML")
    except Exception: pass

async def process_rejection(query, context, order, reason_key):
    order_id = order["order_id"]
    reason = REJECTION_REASONS.get(reason_key, "غير مطابق")
    def mutate(data):
        current = data["pending_payments"].pop(order_id, None)
        if current:
            current["status"] = "rejected"
            current["rejection_reason"] = reason
            data["orders"][order_id] = current
            data["stats"]["rejected_orders"] = safe_int(data["stats"].get("rejected_orders"), 0) + 1
    await update_db(mutate)
    
    try: await context.bot.send_message(chat_id=order["user_id"], text=f"❌ <b>تم رفض الإيصال</b>\nالسبب: {reason}\n\nتواصل مع الدعم للمساعدة.", parse_mode="HTML", reply_markup=support_keyboard())
    except Exception: pass
    try: await query.edit_message_caption(caption=f"❌ تم الرفض بسبب: {reason}", parse_mode="HTML")
    except Exception: pass

# =====================================================
# Commands
# =====================================================
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_session(user, {
        "awaiting_broadcast": False, 
        "awaiting_support_msg": False, 
        "awaiting_voucher": False, 
        "replying_to_user": False,
        "adding_game_step": None,
        "new_game_data": None
    })
    await update.message.reply_text("✅ تم إلغاء العمليات والعودة للرئيسية.", reply_markup=main_menu_keyboard(user.id))

# =====================================================
# Main Execution
# =====================================================
async def main():
    global db
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is missing! Set it in environment variables.")
    
    db = load_db_sync()
    
    # Enable Job Queue via ApplicationBuilder
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    if application.job_queue:
        application.job_queue.run_repeating(auto_backup_job, interval=43200, first=60)
    else:
        logger.warning("JobQueue is not initialized. Make sure 'apscheduler' is installed (pip install python-telegram-bot[job-queue]).")
    
    await application.bot.set_my_commands([BotCommand("start", "القائمة الرئيسية"), BotCommand("cancel", "إلغاء أي عملية")], scope=BotCommandScopeDefault())
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("PlayZone Ultra Professional V2.5 is running...")
    await application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))
        keep_alive()
        nest_asyncio.apply()
        asyncio.run(main())
    except Exception as error:
        logger.exception(f"Fatal error: {error}")
