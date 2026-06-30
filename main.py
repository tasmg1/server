import os
import sys
import json
import time
import html
import signal
import asyncio
import logging
import re
import copy
import uuid
import threading
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from typing import Any, Dict, Optional, List

# مكتبات البوت والويب
import nest_asyncio
from flask import Flask, request, jsonify, redirect, render_template_string

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
# PlayZone Unified Server (Bot + Download Web App)
# Ultimate Professional Version
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("playzone-unified")

# --- المتغيرات الأساسية ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID", "8569699093").strip()

PAYMENT_CARD = os.getenv("PAYMENT_CARD", "7113282938").strip()
GAME_PRICE = os.getenv("GAME_PRICE", "1000 IQD").strip()
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://instagram.com/p1ay.zone").strip()
BOT_USERNAME_URL = os.getenv("BOT_URL", "https://t.me/P1ay_Z0ne_Bot").strip()

# الدومين الخاص بـ Railway (هام جداً ليعمل رابط التحميل)
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8080").strip().rstrip("/")

# --- إعدادات النظام والروابط ---
DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DB_FILE = DATA_DIR / "playzone_bot_data.json"

PAYMENT_TIMEOUT_SECONDS = int(os.getenv("PAYMENT_TIMEOUT_SECONDS", str(60 * 60 * 24)))
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", str(60 * 60 * 2)))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "3"))
MAX_OPENS = int(os.getenv("MAX_OPENS", "5"))
LINK_EXPIRE_SECONDS = int(os.getenv("LINK_EXPIRE_SECONDS", str(60 * 10))) # 10 دقائق

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
except ValueError as exc:
    raise RuntimeError("ADMIN_CHAT_ID must be an integer") from exc

def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_CHAT_ID)

# =====================================================
# Database & Thread-Safe System
# =====================================================

GAMES = {
    "thechallenge": {
        "title": "The Challenge",
        "emoji": "🎮",
        "description": "مغامرة مليئة بالتحديات والألغاز الشيقة.",
        "available_devices": ["android", "ios"],
        "android": "https://www.dropbox.com/scl/fi/3erw8rjjv3gcx01op7iu0/The-Challenge.apk?rlkey=1nirp9ergdym8w7rsmfphrmcg&st=bbc3lwlh&dl=0",
        "ios": "https://www.dropbox.com/scl/fi/3erw8rjjv3gcx01op7iu0/The-Challenge.apk?rlkey=1nirp9ergdym8w7rsmfphrmcg&st=bbc3lwlh&dl=0",
    },
    "chickenlife": {
        "title": "Chicken Life",
        "emoji": "🐔",
        "description": "محاكاة ممتعة لعالم الدجاج مع تحديات مرحة.",
        "available_devices": ["android", "ios"],
        "android": "https://www.dropbox.com/scl/fi/0v4lovtvvlxsuezu3jerh/Chicken-Life.apk?rlkey=qhzz0i6ta2l7ne6ppmd1x5157&st=uxhjejmp&dl=0",
        "ios": "https://www.dropbox.com/scl/fi/0v4lovtvvlxsuezu3jerh/Chicken-Life.apk?rlkey=qhzz0i6ta2l7ne6ppmd1x5157&st=uxhjejmp&dl=0",
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
    "temporary_links": {}, # مدمج من ملف الروابط
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

# قفل متزامن ليسمح لخادم Flask وللبوت بتعديل البيانات دون تعارض
_sync_db_lock = threading.Lock()
db: Dict[str, Any] = copy.deepcopy(DEFAULT_DB)

def merge_defaults(base: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in loaded.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged

def load_db_sync() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        return copy.deepcopy(DEFAULT_DB)
    try:
        with DB_FILE.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except Exception as error:
        logger.error("Could not read DB file: %s", error)
        return copy.deepcopy(DEFAULT_DB)
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

def update_db_sync(mutator):
    """تعديل البيانات بشكل آمن من مسار Flask المتزامن"""
    with _sync_db_lock:
        result = mutator(db)
        save_db_sync()
        return result

async def update_db(mutator):
    """تعديل البيانات بشكل آمن من مسار البوت غير المتزامن"""
    return await asyncio.to_thread(update_db_sync, mutator)

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

def get_game_title(game_id: str) -> str:
    return GAMES.get(game_id, {}).get("title", game_id or "غير معروف")

def get_device_title(device_code: str) -> str:
    return DEVICES.get(device_code, device_code or "غير معروف")

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def add_audit(data: Dict[str, Any], action: str, details: Dict[str, Any]) -> None:
    item = {"time": iso_now(), "action": action, "details": details}
    data.setdefault("audit_log", []).append(item)
    data["audit_log"] = data["audit_log"][-5000:]

def direct_dropbox_url(url: str) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["dl"] = "1"
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(query), parts.fragment))

def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"

async def cleanup_expired_data() -> None:
    now = utc_now_ts()

    def mutate(data):
        # 1. تنظيف المدفوعات المعلقة
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

        # 2. تنظيف الجلسات المؤقتة
        expired_sessions = [
            key for key, session in data["sessions"].items()
            if now - safe_int(session.get("updated_ts", session.get("created_ts", now)), now) > SESSION_TIMEOUT_SECONDS
        ]
        for key in expired_sessions:
            data["sessions"].pop(key, None)

        # 3. تنظيف قيود الاستخدام
        old_rate_limits = [
            key for key, ts in data["rate_limits"].items()
            if now - safe_int(ts, now) > RATE_LIMIT_SECONDS * 10
        ]
        for key in old_rate_limits:
            data["rate_limits"].pop(key, None)
            
        # 4. تنظيف روابط التحميل المؤقتة المنتهية
        removed_links = []
        links_db = data.setdefault("temporary_links", {})
        for token, item in list(links_db.items()):
            if now > int(item.get("expires_at", 0)) + 3600:
                removed_links.append(token)
        for token in removed_links:
            links_db.pop(token, None)

        return len(expired_payments), len(expired_sessions), len(removed_links)

    r_pay, r_ses, r_links = await update_db(mutate)
    if r_pay or r_ses or r_links:
        logger.info("Cleaned up: payments=%s sessions=%s links=%s", r_pay, r_ses, r_links)

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

# =====================================================
# Flask Web Server (Download API & Pages)
# =====================================================
app = Flask(__name__)

PAGE_TEMPLATE = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root { --bg:#070713; --card:#14142b; --cyan:#00eaff; --pink:#ff2d8f; --text:#f2f4ff; --muted:#a8abc9; --warn:#ffcc66; --red:#ff5a72; --ok:#32e887; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; font-family: Arial, Tahoma, sans-serif; background: radial-gradient(circle at 20% 10%, rgba(0,234,255,.22), transparent 28%), radial-gradient(circle at 80% 80%, rgba(255,45,143,.18), transparent 30%), var(--bg); color:var(--text); display:flex; align-items:center; justify-content:center; padding:22px; }
    .card { width:min(560px, 100%); background:linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.025)); border:1px solid rgba(0,234,255,.28); border-radius:28px; padding:28px 22px; text-align:center; box-shadow:0 0 35px rgba(0,234,255,.14), 0 18px 60px rgba(0,0,0,.45); }
    .brand { font-size:30px; font-weight:900; letter-spacing:.3px; color:var(--cyan); text-shadow:0 0 16px rgba(0,234,255,.65); margin-bottom:8px; }
    .icon { width:84px; height:84px; border-radius:26px; margin:0 auto 18px; display:grid; place-items:center; font-size:42px; background:rgba(0,234,255,.08); border:1px solid rgba(0,234,255,.35); box-shadow:inset 0 0 18px rgba(0,234,255,.12); }
    h1 { margin:8px 0 10px; font-size:28px; }
    p { color:var(--muted); line-height:1.9; font-size:16px; margin:8px 0; }
    .info { margin:18px auto; padding:14px; border-radius:18px; background:rgba(0,0,0,.22); border:1px solid rgba(255,255,255,.08); text-align:right; }
    .row { display:flex; justify-content:space-between; gap:12px; padding:7px 0; border-bottom:1px solid rgba(255,255,255,.06); }
    .row:last-child { border-bottom:0; }
    .label { color:var(--muted); }
    .value { font-weight:800; color:var(--text); }
    .actions { display:grid; gap:12px; margin-top:18px; }
    a.btn { display:block; text-decoration:none; border-radius:18px; padding:14px 16px; font-weight:900; color:#07101c; background:linear-gradient(90deg, var(--cyan), #62f7ff); box-shadow:0 10px 24px rgba(0,234,255,.2); }
    a.btn.secondary { background:transparent; color:var(--text); border:1px solid rgba(255,255,255,.18); box-shadow:none; }
    .hint { font-size:13px; color:var(--muted); margin-top:16px; }
  </style>
</head>
<body>
  <main class="card">
    <div class="brand">PlayZone</div>
    <div class="icon">{{ icon }}</div>
    <h1>{{ heading }}</h1>
    <p>{{ message }}</p>
    {% if game_title or order_id %}
    <div class="info">
      {% if order_id %}<div class="row"><span class="label">رقم الطلب</span><span class="value">{{ order_id }}</span></div>{% endif %}
      {% if game_title %}<div class="row"><span class="label">اللعبة</span><span class="value">{{ game_title }}</span></div>{% endif %}
      {% if opens is not none %}<div class="row"><span class="label">الاستخدام</span><span class="value">{{ opens }} / {{ max_opens }}</span></div>{% endif %}
    </div>
    {% endif %}
    <div class="actions">
      <a class="btn" href="{{ bot_url }}">طلب رابط جديد من البوت</a>
      <a class="btn secondary" href="{{ support_url }}">التواصل مع الدعم</a>
    </div>
    <div class="hint">الروابط المؤقتة مخصصة لجهازك ولمدة قصيرة فقط.</div>
  </main>
</body>
</html>
"""

def render_status_page(kind: str, item: dict | None = None, status_code: int = 200):
    item = item or {}
    data = {
        "expired": ("⏳", "انتهت صلاحية الرابط", "انتهى وقت رابط التحميل المؤقت. يرجى طلب رابط جديد من البوت."),
        "limit": ("🚫", "تم تجاوز عدد الفتحات", "تم استخدام الرابط أكثر من العدد المسموح. لحماية الملف، اطلب رابطًا جديدًا من البوت."),
        "forbidden": ("🔒", "الرابط مخصص لجهاز آخر", "هذا الرابط تم فتحه من جهاز مختلف، ولا يمكن استخدامه هنا."),
        "invalid": ("❌", "رابط غير صحيح", "رابط التحميل غير صحيح أو تم حذفه."),
        "error": ("⚠️", "حدث خطأ", "تعذر تجهيز التحميل حاليًا. يرجى التواصل مع الدعم."),
    }
    icon, heading, message = data.get(kind, data["error"])
    html = render_template_string(
        PAGE_TEMPLATE,
        title=f"PlayZone - {heading}",
        icon=icon,
        heading=heading,
        message=message,
        order_id=item.get("order_id"),
        game_title=item.get("game_title"),
        opens=item.get("opens") if item else None,
        max_opens=item.get("max_opens", MAX_OPENS) if item else MAX_OPENS,
        bot_url=BOT_USERNAME_URL,
        support_url=SUPPORT_URL,
    )
    return html, status_code

@app.route("/")
def home():
    return "✅ PlayZone Unified Bot & Download Server is alive!"

@app.route("/health")
def health():
    return jsonify({"success": True, "server": "online", "bot": "PlayZone", "time": iso_now()})

@app.route("/download/<token>")
def download_route(token: str):
    # استخدام المتغير db المشترك بشكل آمن متزامن
    with _sync_db_lock:
        links_db = db.get("temporary_links", {})
        item = links_db.get(token)
    
    if not item:
        return render_status_page("invalid", status_code=404)

    current = utc_now_ts()
    
    # التحقق من الصلاحية
    if current > int(item.get("expires_at", 0)):
        def remove_expired(data):
            data.get("temporary_links", {}).pop(token, None)
        update_db_sync(remove_expired)
        return render_status_page("expired", item, status_code=410)

    # التحقق من عدد الفتحات
    opens = int(item.get("opens", 0))
    max_opens = int(item.get("max_opens", MAX_OPENS))
    if opens >= max_opens:
        return render_status_page("limit", item, status_code=403)

    # التحقق من الـ IP
    ip = client_ip()
    first_ip = item.get("first_ip") or ""
    
    def update_open_count(data):
        link = data.setdefault("temporary_links", {}).get(token)
        if not link: return False, "invalid"
        if not first_ip:
            link["first_ip"] = ip
        elif first_ip != ip:
            return False, "forbidden"
            
        link["opens"] = opens + 1
        link["last_open_at"] = current
        return True, "ok"
        
    success, status = update_db_sync(update_open_count)
    
    if not success and status == "forbidden":
        return render_status_page("forbidden", item, status_code=403)
        
    return redirect(item["source_url"], code=302)

@app.errorhandler(404)
def not_found(_error):
    return render_status_page("invalid", status_code=404)

def run_web_server():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    thread = threading.Thread(target=run_web_server, daemon=True)
    thread.start()

# =====================================================
# Internal Generator Method
# =====================================================
async def create_local_download_link(user_id: int, game_id: str, device_code: str, order_id: str) -> Optional[str]:
    """ينشئ رابط التحميل داخلياً ويحفظه في نفس قاعدة بيانات البوت"""
    source_url = GAMES.get(game_id, {}).get(device_code) or GAMES.get(game_id, {}).get("android")
    if not source_url:
        return None
        
    token = uuid.uuid4().hex + uuid.uuid4().hex[:10]
    created_at = utc_now_ts()
    
    def mutate(data):
        links = data.setdefault("temporary_links", {})
        links[token] = {
            "token": token,
            "game": game_id,
            "game_title": GAMES[game_id]["title"],
            "device": device_code,
            "user_id": str(user_id),
            "order_id": order_id,
            "source_url": direct_dropbox_url(source_url),
            "created_at": created_at,
            "expires_at": created_at + LINK_EXPIRE_SECONDS,
            "opens": 0,
            "max_opens": MAX_OPENS,
            "first_ip": "",
            "last_open_at": 0,
        }
    await update_db(mutate)
    return f"{PUBLIC_URL}/download/{token}"

# =====================================================
# Bot Business Logic (Sessions, Keyboards, Text)
# =====================================================

async def register_user(user) -> None:
    if is_admin(user.id): return
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
            "user_id": user.id, "full_name": user.full_name,
            "username": user.username or "", "created_ts": utc_now_ts(), "history": [],
        })
        session.update({"full_name": user.full_name, "username": user.username or "", "updated_ts": utc_now_ts()})
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
        session = data.setdefault("sessions", {}).setdefault(key, {"user_id": user_id, "created_ts": utc_now_ts(), "history": []})
        session["last_menu_message_id"] = message_id
        session["updated_ts"] = utc_now_ts()
    await update_db(mutate)

def get_session(user_id: int) -> Dict[str, Any]:
    return db.get("sessions", {}).get(user_key(user_id), {})

async def delete_last_menu_if_possible(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    session = get_session(user_id)
    msg_id = session.get("last_menu_message_id")
    if not msg_id: return
    try: await context.bot.delete_message(chat_id=chat_id, message_id=int(msg_id))
    except Exception: pass

def get_user_orders(user_id: int) -> List[Dict[str, Any]]:
    orders = [order for order in db.get("orders", {}).values() if str(order.get("user_id")) == str(user_id)]
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

# --- Keyboards ---
def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup: return InlineKeyboardMarkup(rows)

def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎮 الألعاب", callback_data="menu:games"), InlineKeyboardButton("📦 حالة طلبي", callback_data="menu:status")],
        [InlineKeyboardButton("🕹️ ألعابي", callback_data="menu:my_games"), InlineKeyboardButton("💳 الدفع", callback_data="menu:payment")],
        [InlineKeyboardButton("❓ المساعدة", callback_data="menu:help"), InlineKeyboardButton("📞 الدعم", callback_data="menu:support")],
    ]
    if is_admin(user_id): rows.append([InlineKeyboardButton("👑 لوحة الأدمن", callback_data="admin:panel")])
    return kb(rows)

def games_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{game['emoji']} {game['title']}", callback_data=f"game:{game_id}")] for game_id, game in GAMES.items()]
    rows.append([InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")])
    return kb(rows)

def devices_keyboard(game_id: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(DEVICES[dc], callback_data=f"device:{game_id}:{dc}")] for dc in GAMES[game_id].get("available_devices", [])]
    rows.append([InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")])
    return kb(rows)

def payment_keyboard() -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton("📸 أرسلت الإيصال", callback_data="pay:receipt_help")], [InlineKeyboardButton("🎮 تغيير اللعبة", callback_data="menu:games")], [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")]])

def support_keyboard() -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton("📞 تواصل عبر إنستغرام", url=SUPPORT_URL)], [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")]])

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats"), InlineKeyboardButton("⏳ الطلبات", callback_data="admin:pending")],
        [InlineKeyboardButton("🧾 آخر الطلبات", callback_data="admin:orders"), InlineKeyboardButton("📤 تصدير البيانات", callback_data="admin:export")],
        [InlineKeyboardButton("♻️ تصفير العدادات", callback_data="admin:reset_confirm"), InlineKeyboardButton("🧹 حذف الجلسات", callback_data="admin:clear_sessions_confirm")],
        [InlineKeyboardButton("📢 إرسال تنبيه", callback_data="admin:broadcast_prompt"), InlineKeyboardButton("🏠 القائمة", callback_data="menu:home")],
    ])

def admin_review_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("✅ قبول وإرسال زر التحميل", callback_data=f"admin:approve:{order_id}")],
        [InlineKeyboardButton("❌ رفض الإيصال", callback_data=f"admin:reject_menu:{order_id}"), InlineKeyboardButton("ℹ️ معلومات", callback_data=f"admin:info:{order_id}")],
        [InlineKeyboardButton("👑 لوحة الأدمن", callback_data="admin:panel")],
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
        [InlineKeyboardButton("📲 طريقة التثبيت", callback_data=f"download:install:{order_id}"), InlineKeyboardButton("📦 حالة الطلب", callback_data=f"download:status:{order_id}")],
        [InlineKeyboardButton("📞 أحتاج مساعدة", callback_data=f"download:support:{order_id}")],
    ])

def download_back_keyboard(order_id: str) -> InlineKeyboardMarkup: return kb([[InlineKeyboardButton("⬅️ رجوع لزر التحميل", callback_data=f"download:back:{order_id}")]])
def download_support_keyboard(order_id: str) -> InlineKeyboardMarkup: return kb([[InlineKeyboardButton("📞 تواصل عبر إنستغرام", url=SUPPORT_URL)], [InlineKeyboardButton("⬅️ رجوع لزر التحميل", callback_data=f"download:back:{order_id}")]])
def receipt_submitted_keyboard(order_id: str) -> InlineKeyboardMarkup: return kb([[InlineKeyboardButton("📦 متابعة حالة الطلب", callback_data=f"receipt:status:{order_id}")], [InlineKeyboardButton("📞 الدعم", callback_data=f"receipt:support:{order_id}")], [InlineKeyboardButton("🎮 اختيار لعبة أخرى", callback_data="menu:games")]])
def receipt_back_keyboard(order_id: str) -> InlineKeyboardMarkup: return kb([[InlineKeyboardButton("⬅️ رجوع لرسالة الإيصال", callback_data=f"receipt:back:{order_id}")]])
def order_support_keyboard(order_id: str) -> InlineKeyboardMarkup: return kb([[InlineKeyboardButton("📞 تواصل عبر إنستغرام", url=SUPPORT_URL)], [InlineKeyboardButton("⬅️ رجوع لرسالة الإيصال", callback_data=f"receipt:back:{order_id}")]])
def confirm_reset_keyboard() -> InlineKeyboardMarkup: return kb([[InlineKeyboardButton("✅ نعم، صفّر العدادات", callback_data="admin:reset_stats")], [InlineKeyboardButton("❌ إلغاء", callback_data="admin:panel")]])
def confirm_clear_sessions_keyboard() -> InlineKeyboardMarkup: return kb([[InlineKeyboardButton("✅ نعم، احذف الجلسات", callback_data="admin:clear_sessions")], [InlineKeyboardButton("❌ إلغاء", callback_data="admin:panel")]])

# --- Texts ---
def home_text(user_id: int) -> str:
    return "👋 <b>أهلاً بك في PlayZone</b>\n\nمن هنا يمكنك طلب ألعاب الموبايل بطريقة منظمة وآمنة.\n\nاختر من الأزرار بالأسفل:" + ("\n\n👑 <b>وضع الأدمن مفعل.</b>" if is_admin(user_id) else "")

def games_text() -> str: return "🎮 <b>الألعاب المتوفرة</b>\n\nاختر اللعبة التي تريد تحميلها:"
def game_details_text(game_id: str) -> str: return f"{GAMES[game_id]['emoji']} <b>{escape_text(GAMES[game_id]['title'])}</b>\n\n{escape_text(GAMES[game_id]['description'])}\n\nاختر نوع جهازك:"
def payment_text(game_title: Optional[str] = None, device_title: Optional[str] = None) -> str:
    lines = ["💳 <b>إتمام الدفع</b>", ""]
    if game_title and device_title: lines.extend(["🎮 <b>اللعبة:</b> " + escape_text(game_title), "📱 <b>الجهاز:</b> " + escape_text(device_title)])
    lines.extend(["💰 <b>السعر:</b> " + escape_text(GAME_PRICE), "", "حوّل المبلغ إلى بطاقة ماستر كارد التالية:", "<code>" + escape_text(PAYMENT_CARD) + "</code>", "", "📸 بعد التحويل، أرسل صورة الإيصال هنا في المحادثة.", "", "✅ بعد موافقة الإدارة سيصلك زر تحميل مؤقت خاص بك.", "⚠️ تأكد أن الإيصال واضح ويظهر مبلغ التحويل."])
    return "\n".join(lines)

def receipt_help_text() -> str: return "📸 <b>إرسال الإيصال</b>\n\nأرسل صورة إيصال الدفع هنا في نفس المحادثة.\n\nنصائح لقبول أسرع:\n• اجعل الصورة واضحة.\n• يجب أن يظهر مبلغ التحويل.\n• لا ترسل أكثر من صورة لنفس الطلب.\n• انتظر مراجعة الإدارة."
def order_status_text(order: Dict[str, Any]) -> str:
    lines = ["📦 <b>حالة الطلب</b>", "", "🧾 <b>رقم الطلب:</b> " + escape_text(order.get("order_id", "")), "🎮 <b>اللعبة:</b> " + escape_text(get_game_title(order.get("game"))), "📱 <b>الجهاز:</b> " + escape_text(get_device_title(order.get("device"))), "💰 <b>السعر:</b> " + escape_text(order.get("price", GAME_PRICE)), "📌 <b>الحالة:</b> " + escape_text(STATUS_LABELS.get(order.get("status", "pending"), order.get("status", "pending")))]
    if order.get("rejection_reason"): lines.append("❗ <b>سبب الرفض:</b> " + escape_text(order.get("rejection_reason")))
    if order.get("created_at_text"): lines.append("🕒 <b>وقت الطلب:</b> " + escape_text(order.get("created_at_text")))
    return "\n".join(lines)

def receipt_received_text(order: Dict[str, Any]) -> str: return "✅ <b>تم استلام إيصال الدفع بنجاح</b>\n\n🧾 رقم الطلب: <code>" + escape_text(order.get("order_id", "")) + "</code>\n🎮 اللعبة: <b>" + escape_text(get_game_title(order.get("game"))) + "</b>\n📱 الجهاز: <b>" + escape_text(get_device_title(order.get("device"))) + "</b>\n💰 السعر: <b>" + escape_text(order.get("price", GAME_PRICE)) + "</b>\n\n📌 الحالة: <b>قيد المراجعة</b>\nبعد الموافقة سيصلك زر التحميل هنا مباشرة."
def no_order_text() -> str: return "📦 <b>حالة طلبي</b>\n\nلا يوجد لديك طلب حاليًا.\n\nابدأ باختيار لعبة من زر <b>الألعاب</b>."
def my_games_text(user_id: int) -> str:
    orders = [order for order in get_user_orders(user_id) if order.get("status") == "approved"]
    if not orders: return "🕹️ <b>ألعابي</b>\n\nلا توجد ألعاب مقبولة سابقًا في حسابك حتى الآن."
    lines = ["🕹️ <b>ألعابك السابقة</b>", ""]
    for idx, order in enumerate(orders[:10], start=1): lines.append(f"{idx}. {escape_text(get_game_title(order.get('game')))} - {escape_text(get_device_title(order.get('device')))}")
    lines.extend(["", "لرابط جديد، اطلب اللعبة مرة أخرى أو تواصل مع الدعم."])
    return "\n".join(lines)

def help_text() -> str: return "❓ <b>المساعدة</b>\n\nطريقة الطلب:\n1️⃣ اختر اللعبة.\n2️⃣ اختر نوع جهازك.\n3️⃣ حوّل مبلغ اللعبة.\n4️⃣ أرسل صورة الإيصال.\n5️⃣ بعد الموافقة يصلك زر التحميل.\n\n🔐 رابط التحميل مؤقت وخاص بك.\n📌 لا تشارك الرابط مع أي شخص."
def download_ready_text(order: Dict[str, Any]) -> str: return "✅ <b>طلبك جاهز</b>\n\n🧾 رقم الطلب: <code>" + escape_text(order.get("order_id", "")) + "</code>\n🎮 اللعبة: <b>" + escape_text(get_game_title(order.get("game"))) + "</b>\n📱 الجهاز: <b>" + escape_text(get_device_title(order.get("device"))) + "</b>\n\nاضغط زر التحميل بالأسفل.\n⚠️ الرابط مؤقت وخاص بك، لا تشاركه مع أي شخص."
def support_text(user_id: int) -> str:
    latest = get_user_latest_order(user_id)
    lines = ["📞 <b>الدعم</b>", "", "للمساعدة اضغط زر التواصل بالأسفل."]
    if latest: lines.extend(["", "آخر طلب لديك:", "🧾 " + escape_text(latest.get("order_id")), "🎮 " + escape_text(get_game_title(latest.get("game"))), "📌 " + escape_text(STATUS_LABELS.get(latest.get("status", "pending"), latest.get("status", "pending")))])
    return "\n".join(lines)

def install_instructions_text(device_code: Optional[str] = None) -> str:
    if device_code == "ios": return "🍎 <b>ملاحظة iPhone</b>\n\nإذا لم يعمل التحميل على iPhone، تواصل مع الدعم ليتم إرشادك للطريقة المناسبة."
    return "📲 <b>طريقة تثبيت Android</b>\n\n1️⃣ اضغط زر التحميل.\n2️⃣ انتظر اكتمال تحميل ملف APK.\n3️⃣ افتح الملف.\n4️⃣ إذا ظهر تحذير، فعّل التثبيت من مصادر غير معروفة.\n5️⃣ اضغط تثبيت ثم افتح اللعبة."

def order_caption(order: Dict[str, Any]) -> str:
    username = "@" + order.get("username") if order.get("username") else "لا يوجد"
    return f"📩 <b>مراجعة إيصال دفع جديد</b>\n\n🧾 رقم الطلب: <code>{escape_text(order.get('order_id'))}</code>\n👤 الاسم: {escape_text(order.get('full_name'))}\n🔗 username: {escape_text(username)}\n🆔 ID: <code>{escape_text(order.get('user_id'))}</code>\n🎮 اللعبة: {escape_text(get_game_title(order.get('game')))}\n📱 الجهاز: {escape_text(get_device_title(order.get('device')))}\n💰 السعر: {escape_text(order.get('price', GAME_PRICE))}\n🕒 الوقت: {escape_text(order.get('created_at_text'))}\n📌 الحالة: {escape_text(STATUS_LABELS.get(order.get('status', 'pending'), order.get('status', 'pending')))}"

def admin_stats_text() -> str:
    stats, pending, orders, sessions, users = db.get("stats", {}), db.get("pending_payments", {}), db.get("orders", {}), db.get("sessions", {}), db.get("users", {})
    sales = safe_int(stats.get("approved_orders"), 0) * extract_price_number()
    return f"📊 <b>إحصائيات PlayZone</b>\n\n👥 المستخدمون: <b>{len(users)}</b>\n🧾 الطلبات الكلية: <b>{len(orders)}</b>\n⏳ قيد المراجعة: <b>{len(pending)}</b>\n✅ المقبولة: <b>{safe_int(stats.get('approved_orders'), 0)}</b>\n❌ المرفوضة: <b>{safe_int(stats.get('rejected_orders'), 0)}</b>\n🔗 روابط مولدة: <b>{safe_int(stats.get('generated_links'), 0)}</b>\n⚠️ فشل الروابط: <b>{safe_int(stats.get('link_failures'), 0)}</b>\n🧠 جلسات مؤقتة: <b>{len(sessions)}</b>\n💰 المبيعات التقريبية: <b>{sales} IQD</b>"

def admin_pending_text() -> str:
    pending = list(db.get("pending_payments", {}).values())
    pending.sort(key=lambda item: safe_int(item.get("created_ts"), 0), reverse=True)
    if not pending: return "⏳ <b>الطلبات قيد المراجعة</b>\n\nلا توجد طلبات حاليًا."
    return "⏳ <b>الطلبات قيد المراجعة</b>\n\n" + "\n".join([f"• <code>{escape_text(o.get('order_id'))}</code> | {escape_text(get_game_title(o.get('game')))} | {escape_text(get_device_title(o.get('device')))}" for o in pending[:20]])

def admin_orders_text() -> str:
    orders = list(db.get("orders", {}).values())
    orders.sort(key=lambda item: safe_int(item.get("created_ts"), 0), reverse=True)
    if not orders: return "🧾 <b>آخر الطلبات</b>\n\nلا توجد طلبات محفوظة."
    return "🧾 <b>آخر الطلبات</b>\n\n" + "\n".join([f"{escape_text(o.get('order_id'))} | {escape_text(get_game_title(o.get('game')))} | {escape_text(STATUS_LABELS.get(o.get('status', 'pending'), o.get('status', 'pending')))}" for o in orders[:15]])

# --- UI Methods ---
async def send_new_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    if not update.message: return
    user, chat_id = update.message.from_user, update.message.chat_id
    await delete_last_menu_if_possible(context, chat_id, user.id)
    msg = await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    await set_last_menu_message(user.id, msg.message_id)

async def edit_query_message(query, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try: await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    except BadRequest as error:
        if "Message is not modified" in str(error): return
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception: await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)

async def show_home_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message: await send_new_menu(update, context, home_text(update.message.from_user.id), main_menu_keyboard(update.message.from_user.id))
async def show_home_from_query(query) -> None: await edit_query_message(query, home_text(query.from_user.id), main_menu_keyboard(query.from_user.id))

# =====================================================
# Bot Handlers
# =====================================================
async def setup_bot_commands(application):
    await application.bot.set_my_commands([BotCommand("start", "فتح القائمة الرئيسية"), BotCommand("cancel", "إلغاء الاختيار الحالي"), BotCommand("help", "شرح طريقة الطلب")], scope=BotCommandScopeDefault())
    await application.bot.set_my_commands([BotCommand("start", "فتح القائمة الرئيسية"), BotCommand("admin", "لوحة الأدمن"), BotCommand("stats", "إحصائيات البوت"), BotCommand("pending", "الطلبات قيد المراجعة"), BotCommand("orders", "آخر الطلبات"), BotCommand("reset_stats", "تصفير عدادات البوت")], scope=BotCommandScopeChat(chat_id=ADMIN_CHAT_ID))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_expired_data()
    if not update.message: return
    user = update.message.from_user
    await register_user(user)
    await upsert_session(user, {}, push_screen="home")
    if context.args:
        payload = str(context.args[0] or "").strip().lower().replace("-", "").replace("_", "")
        if payload in GAMES:
            await upsert_session(user, {"game": payload}, push_screen="game")
            await send_new_menu(update, context, game_details_text(payload), devices_keyboard(payload))
            return
    await show_home_from_update(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message: await send_new_menu(update, context, help_text(), kb([[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user = update.message.from_user
    def mutate(data):
        session = data.get("sessions", {}).setdefault(user_key(user.id), {})
        for key in ["game", "device", "awaiting_broadcast"]: session.pop(key, None)
        session["history"] = ["home"]
        session["updated_ts"] = utc_now_ts()
    await update_db(mutate)
    await send_new_menu(update, context, "✅ تم إلغاء الاختيار الحالي.\n\nاختر من القائمة:", main_menu_keyboard(user.id))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user = update.message.from_user
    session = get_session(user.id)
    if is_admin(user.id) and session.get("awaiting_broadcast"):
        await handle_admin_broadcast_text(update, context, update.message.text or "")
        return
    if await is_rate_limited(user.id, "text"): return
    await send_new_menu(update, context, "استخدم الأزرار بالأسفل لاختيار اللعبة وإتمام الطلب.\n\nإذا كنت تريد الدفع، اختر اللعبة أولًا ثم أرسل صورة الإيصال.", main_menu_keyboard(user.id))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user = update.message.from_user
    if is_admin(user.id):
        await update.message.reply_text("👑 وضع الأدمن مفعّل.\nلن يتم احتساب هذه الصورة كطلب حقيقي.")
        return
    if await is_rate_limited(user.id, "photo"):
        await update.message.reply_text("⏳ انتظر قليلًا قبل إرسال إيصال آخر.")
        return

    session = db.get("sessions", {}).get(user_key(user.id), {})
    game_id, device_code = session.get("game"), session.get("device")
    if not game_id or not device_code:
        await send_new_menu(update, context, "⚠️ اختر اللعبة ونوع الجهاز أولًا، ثم أرسل إيصال الدفع.", games_keyboard())
        return

    existing_pending = get_pending_user_order(user.id)
    if existing_pending:
        await send_new_menu(update, context, "⏳ لديك طلب قيد المراجعة حاليًا.\n\n" + order_status_text(existing_pending), kb([[InlineKeyboardButton("📦 متابعة حالة الطلب", callback_data=f"receipt:status:{existing_pending.get('order_id')}")], [InlineKeyboardButton("📞 الدعم", callback_data=f"receipt:support:{existing_pending.get('order_id')}")]]))
        return

    order_id = await next_order_id()
    order = {
        "order_id": order_id, "file_id": update.message.photo[-1].file_id, "game": game_id, "device": device_code,
        "user_id": user.id, "full_name": user.full_name, "username": user.username or "", "status": "pending",
        "price": GAME_PRICE, "created_ts": utc_now_ts(), "created_at": iso_now(), "created_at_text": now_text(),
    }
    def mutate(data):
        data["pending_payments"][order_id] = order
        data["orders"][order_id] = order.copy()
        data["stats"]["submitted_receipts"] = safe_int(data["stats"].get("submitted_receipts"), 0) + 1
        add_audit(data, "receipt_submitted", {"order_id": order_id, "user_id": user.id})
    await update_db(mutate)

    try:
        await context.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=order["file_id"], caption=order_caption(order), parse_mode="HTML", reply_markup=admin_review_keyboard(order_id))
    except TelegramError as error:
        logger.error("Failed to send receipt to admin: %s", error)
        await update.message.reply_text("⚠️ حدث خطأ أثناء إرسال الإيصال للمراجعة. حاول لاحقًا.")
        return
    await send_new_menu(update, context, receipt_received_text(order), receipt_submitted_keyboard(order_id))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    user_id = query.from_user.id
    if await is_rate_limited(user_id, "button"):
        await query.answer("⏳ يرجى الانتظار قليلاً لتجنب الضغط على السيرفر...", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    try:
        if data.startswith("admin:"): await handle_admin_callback(query, context, data, user_id)
        elif data.startswith("nav:"): await handle_nav_callback(query, context, data)
        elif data.startswith("receipt:"): await handle_receipt_callback(query, context, data)
        elif data.startswith("download:"): await handle_download_callback(query, context, data)
        elif data.startswith("menu:"): await handle_menu_callback(query, context, data)
        elif data.startswith("game:"): await handle_game_callback(query, data)
        elif data.startswith("device:"): await handle_device_callback(query, data)
        elif data == "pay:receipt_help": await edit_query_message(query, receipt_help_text(), payment_keyboard())
        else: await query.answer("خيار غير معروف", show_alert=True)
    except Exception as error:
        logger.exception("button_handler error: %s", error)

async def handle_nav_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    session = get_session(query.from_user.id)
    history = session.get("history", []) or []
    previous = history[-2] if len(history) >= 2 else "home"
    def mutate(data_obj):
        s = data_obj.setdefault("sessions", {}).setdefault(user_key(query.from_user.id), {})
        if len(s.get("history", [])) > 1: s["history"].pop()
        s["updated_ts"] = utc_now_ts()
    await update_db(mutate)
    await show_screen(query, previous, push=False)

async def handle_menu_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    screen = data.split(":", 1)[1]
    if screen == "home":
        def reset_history(data_obj):
            s = data_obj.setdefault("sessions", {}).setdefault(user_key(query.from_user.id), {})
            s["history"] = ["home"]
            s["updated_ts"] = utc_now_ts()
        await update_db(reset_history)
        await show_screen(query, "home", push=False)
        return
    await show_screen(query, screen, push=True)

async def show_screen(query, screen: str, push: bool = True):
    if push: await upsert_session(query.from_user, {}, push_screen=screen)
    if screen == "home": await show_home_from_query(query)
    elif screen == "games": await edit_query_message(query, games_text(), games_keyboard())
    elif screen == "payment":
        session = get_session(query.from_user.id)
        await edit_query_message(query, payment_text(get_game_title(session.get("game")), get_device_title(session.get("device"))), payment_keyboard())
    elif screen == "status":
        latest = get_user_latest_order(query.from_user.id)
        if latest: await edit_query_message(query, order_status_text(latest), kb([[InlineKeyboardButton("🔄 تحديث الحالة", callback_data="menu:status")], [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")]]))
        else: await edit_query_message(query, no_order_text(), kb([[InlineKeyboardButton("🎮 اختر لعبة", callback_data="menu:games")], [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")]]))
    elif screen == "my_games": await edit_query_message(query, my_games_text(query.from_user.id), kb([[InlineKeyboardButton("🎮 طلب لعبة جديدة", callback_data="menu:games")], [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")]]))
    elif screen == "support": await edit_query_message(query, support_text(query.from_user.id), support_keyboard())
    elif screen == "help": await edit_query_message(query, help_text(), kb([[InlineKeyboardButton("🎮 ابدأ الطلب", callback_data="menu:games")], [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")]]))
    elif screen == "install_help": await edit_query_message(query, install_instructions_text(get_session(query.from_user.id).get("device")), kb([[InlineKeyboardButton("📞 الدعم", callback_data="menu:support")], [InlineKeyboardButton("⬅️ رجوع خطوة", callback_data="nav:back")]]))
    else: await show_home_from_query(query)

async def handle_receipt_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    parts = data.split(":", 2)
    action, order_id = parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""
    order = db.get("orders", {}).get(order_id) or db.get("pending_payments", {}).get(order_id)
    if not order:
        await query.answer("لم أجد بيانات هذا الطلب", show_alert=True)
        return
    if str(order.get("user_id")) != str(query.from_user.id) and not is_admin(query.from_user.id):
        await query.answer("هذا الطلب لا يخص حسابك", show_alert=True)
        return

    if action == "back": await edit_query_message(query, receipt_received_text(order), receipt_submitted_keyboard(order_id))
    elif action == "status": await edit_query_message(query, order_status_text(order), receipt_back_keyboard(order_id))
    elif action == "support": await edit_query_message(query, support_text(query.from_user.id), order_support_keyboard(order_id))
    else: await query.answer("خيار غير معروف", show_alert=True)

async def handle_download_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    parts = data.split(":", 2)
    action, order_id = parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""
    order = db.get("orders", {}).get(order_id)
    if not order: return await query.answer("لم أجد بيانات هذا الطلب", show_alert=True)
    if str(order.get("user_id")) != str(query.from_user.id) and not is_admin(query.from_user.id): return await query.answer("هذا الطلب لا يخص حسابك", show_alert=True)

    download_url = order.get("download_url")
    if action == "back":
        if download_url: await edit_query_message(query, download_ready_text(order), download_keyboard(download_url, order_id))
        else: await edit_query_message(query, order_status_text(order), kb([[InlineKeyboardButton("📞 الدعم", callback_data=f"download:support:{order_id}")], [InlineKeyboardButton("⬅️ رجوع لحالة الطلب", callback_data=f"download:status:{order_id}")]]))
    elif action == "install": await edit_query_message(query, install_instructions_text(order.get("device")), kb([[InlineKeyboardButton("⬅️ رجوع لزر التحميل", callback_data=f"download:back:{order_id}")], [InlineKeyboardButton("📞 الدعم", callback_data=f"download:support:{order_id}")]]))
    elif action == "status": await edit_query_message(query, order_status_text(order), download_back_keyboard(order_id))
    elif action == "support": await edit_query_message(query, support_text(query.from_user.id), download_support_keyboard(order_id))
    else: await query.answer("خيار غير معروف", show_alert=True)

async def handle_game_callback(query, data: str):
    game_id = data.split(":", 1)[1]
    if game_id not in GAMES: return await query.answer("هذه اللعبة غير متوفرة حاليًا", show_alert=True)
    await upsert_session(query.from_user, {"game": game_id, "device": None}, push_screen="game")
    await edit_query_message(query, game_details_text(game_id), devices_keyboard(game_id))

async def handle_device_callback(query, data: str):
    _, game_id, device_code = data.split(":")
    if game_id not in GAMES or device_code not in DEVICES: return await query.answer("اختيار غير صحيح", show_alert=True)
    await upsert_session(query.from_user, {"game": game_id, "device": device_code}, push_screen="payment")
    await edit_query_message(query, payment_text(GAMES[game_id]["title"], DEVICES[device_code]), payment_keyboard())

async def handle_admin_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str, user_id: int):
    if not is_admin(user_id): return await query.answer("غير مسموح", show_alert=True)
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "panel": await edit_query_message(query, "👑 <b>لوحة الأدمن</b>\n\nاختر الإجراء المطلوب:", admin_panel_keyboard())
    elif action == "stats": await edit_query_message(query, admin_stats_text(), admin_panel_keyboard())
    elif action == "pending": await edit_query_message(query, admin_pending_text(), admin_panel_keyboard())
    elif action == "orders": await edit_query_message(query, admin_orders_text(), admin_panel_keyboard())
    elif action == "reset_confirm": await edit_query_message(query, "⚠️ <b>تأكيد تصفير العدادات</b>\n\nسيتم تصفير الإحصائيات فقط، ولن يتم حذف الطلبات.\nرقم الطلب التالي سيبقى محفوظًا.", confirm_reset_keyboard())
    elif action == "reset_stats":
        await update_db(lambda d: d.update({"stats": {"started_users": 0, "submitted_receipts": 0, "approved_orders": 0, "rejected_orders": 0, "generated_links": 0, "link_failures": 0, "order_counter": safe_int(d.get("stats", {}).get("order_counter"), 1000)}, "users": {}}))
        await edit_query_message(query, "✅ تم تصفير عدادات البوت بنجاح.\n\n" + admin_stats_text(), admin_panel_keyboard())
    elif action == "clear_sessions_confirm": await edit_query_message(query, "⚠️ <b>تأكيد حذف الجلسات المؤقتة</b>\n\nهذا لا يحذف الطلبات، فقط يمسح اختيارات المستخدمين المؤقتة.", confirm_clear_sessions_keyboard())
    elif action == "clear_sessions":
        await update_db(lambda d: d.update({"sessions": {}, "rate_limits": {}}))
        await edit_query_message(query, "✅ تم حذف الجلسات المؤقتة.", admin_panel_keyboard())
    elif action == "export":
        export_path = DATA_DIR / f"playzone_export_{int(time.time())}.json"
        with export_path.open("w", encoding="utf-8") as file: json.dump(db, file, ensure_ascii=False, indent=2)
        await context.bot.send_document(chat_id=ADMIN_CHAT_ID, document=export_path.open("rb"), filename=export_path.name, caption="📤 نسخة احتياطية من بيانات PlayZone Bot")
        export_path.unlink(missing_ok=True)
    elif action == "broadcast_prompt":
        await upsert_session(query.from_user, {"awaiting_broadcast": True}, push_screen="admin_broadcast")
        await edit_query_message(query, "📢 <b>إرسال تنبيه للمستخدمين</b>\n\nأرسل الآن نص الرسالة في المحادثة.\n\nللإلغاء اكتب /cancel", kb([[InlineKeyboardButton("❌ إلغاء", callback_data="admin:panel")]]))
    else:
        order_id = parts[2] if len(parts) > 2 else ""
        order = db.get("pending_payments", {}).get(order_id)
        if not order: return await query.answer("تمت معالجة الطلب أو غير موجود", show_alert=True)
        
        if action == "info": await query.answer(f"Order: {order_id}\nUser: {mask_user_id(order.get('user_id'))}\nGame: {get_game_title(order.get('game'))}\nDevice: {get_device_title(order.get('device'))}", show_alert=True)
        elif action == "reject_menu": await query.edit_message_caption(order_caption(order) + "\n\nاختر سبب الرفض:", parse_mode="HTML", reply_markup=rejection_reasons_keyboard(order_id))
        elif action == "back": await query.edit_message_caption(order_caption(order), parse_mode="HTML", reply_markup=admin_review_keyboard(order_id))
        elif action == "reject_reason":
            reason_text = REJECTION_REASONS.get(parts[3] if len(parts) > 3 else "invalid_image", "تم رفض الإيصال")
            def mutate(data):
                current = data["pending_payments"].pop(order_id, None)
                if current:
                    current["status"], current["reviewed_at"], current["rejection_reason"] = "rejected", iso_now(), reason_text
                    data["orders"][order_id] = current
                data["stats"]["rejected_orders"] = safe_int(data["stats"].get("rejected_orders"), 0) + 1
            await update_db(mutate)
            await context.bot.send_message(chat_id=int(order["user_id"]), text=f"❌ <b>تم رفض إيصال الدفع</b>\n\n🧾 رقم الطلب: <code>{escape_text(order_id)}</code>\nالسبب: {escape_text(reason_text)}\n\nيمكنك إرسال إيصال جديد أو التواصل مع الدعم.", parse_mode="HTML", reply_markup=support_keyboard())
            order["status"], order["rejection_reason"] = "rejected", reason_text
            try: await query.edit_message_caption(order_caption(order) + "\n\n🚫 تم الرفض في " + now_text(), parse_mode="HTML")
            except Exception: pass
        elif action == "approve":
            target_user_id = int(order["user_id"])
            await context.bot.send_message(chat_id=target_user_id, text="✅ تم قبول الدفع.\n\nجاري تجهيز زر التحميل المؤقت الخاص بك...")
            
            # إنتاج الرابط داخلياً بدلاً من الاتصال الخارجي بـ PythonAnywhere
            download_url = await create_local_download_link(target_user_id, order["game"], order["device"], order_id)
            
            if not download_url:
                await update_db(lambda d: d["orders"].update({order_id: {**d["pending_payments"].get(order_id, {}), "status": "link_failed"}}))
                await context.bot.send_message(chat_id=target_user_id, text="⚠️ تم قبول الدفع، لكن حدث خطأ أثناء تجهيز رابط التحميل.\nسيتم حل المشكلة قريبًا.", reply_markup=support_keyboard())
                return await query.answer("فشل توليد الرابط", show_alert=True)
                
            await context.bot.send_message(chat_id=target_user_id, text=f"✅ <b>طلبك جاهز</b>\n\n🧾 رقم الطلب: <code>{escape_text(order_id)}</code>\n🎮 اللعبة: <b>{escape_text(get_game_title(order['game']))}</b>\n📱 الجهاز: <b>{escape_text(get_device_title(order['device']))}</b>\n\nاضغط زر التحميل بالأسفل.\n⚠️ الرابط مؤقت وخاص بك، لا تشاركه مع أي شخص.", parse_mode="HTML", reply_markup=download_keyboard(download_url, order_id))
            
            def mutate(data):
                current = data["pending_payments"].pop(order_id, None)
                if current:
                    current.update({"status": "approved", "reviewed_at": iso_now(), "download_url_generated": True, "download_url": download_url})
                    data["orders"][order_id] = current
                session = data.setdefault("sessions", {}).setdefault(user_key(target_user_id), {})
                session.pop("game", None); session.pop("device", None)
                session["history"], session["updated_ts"] = ["home"], utc_now_ts()
                data["stats"]["approved_orders"] = safe_int(data["stats"].get("approved_orders"), 0) + 1
                data["stats"]["generated_links"] = safe_int(data["stats"].get("generated_links"), 0) + 1
            await update_db(mutate)
            order["status"] = "approved"
            try: await query.edit_message_caption(order_caption(order) + "\n\n✅ تم القبول وإرسال زر التحميل في " + now_text(), parse_mode="HTML")
            except Exception: pass

async def handle_admin_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    users = list(db.get("users", {}).keys())
    sent, failed = 0, 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception: failed += 1
    await update_db(lambda d: d.setdefault("sessions", {}).setdefault(user_key(ADMIN_CHAT_ID), {}).pop("awaiting_broadcast", None))
    await update.message.reply_text(f"📢 تم إرسال التنبيه.\n\n✅ وصل: {sent}\n⚠️ فشل: {failed}", reply_markup=admin_panel_keyboard())

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and is_admin(update.message.from_user.id): await send_new_menu(update, context, "👑 <b>لوحة الأدمن</b>\n\nاختر الإجراء:", admin_panel_keyboard())
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and is_admin(update.message.from_user.id): await cleanup_expired_data(); await send_new_menu(update, context, admin_stats_text(), admin_panel_keyboard())
async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and is_admin(update.message.from_user.id): await send_new_menu(update, context, admin_pending_text(), admin_panel_keyboard())
async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and is_admin(update.message.from_user.id): await send_new_menu(update, context, admin_orders_text(), admin_panel_keyboard())
async def reset_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and is_admin(update.message.from_user.id):
        await update_db(lambda d: d.update({"stats": {"started_users": 0, "submitted_receipts": 0, "approved_orders": 0, "rejected_orders": 0, "generated_links": 0, "link_failures": 0, "order_counter": safe_int(d.get("stats", {}).get("order_counter"), 1000)}, "users": {}}))
        await send_new_menu(update, context, "✅ تم تصفير عدادات البوت.\n\n" + admin_stats_text(), admin_panel_keyboard())
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE): logger.exception("Unhandled error: %s", context.error)

# =====================================================
# Main
# =====================================================
async def main():
    global db
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is missing.")
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

    logger.info("PlayZone Unified server is running...")
    await application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))

        # Start Flask Web Server
        keep_alive()
        nest_asyncio.apply()

        # Start Bot
        logger.info("Starting PlayZone Unified bot...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as error:
        logger.exception("General error: %s", error)
