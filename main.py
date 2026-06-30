import os
import sys
import json
import time
import hmac
import html
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from threading import Thread
from typing import Any, Dict, Optional, List

import aiohttp
from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeDefault,
)
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# =====================================================
# PlayZone Telegram Bot - The Masterpiece (V3.0 Final)
# Zero-Defect | CRM | Anti-Share | Dynamic Settings
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("playzone-bot")

# --- Safe Environment Loaders ---
def get_env_int(key: str, default: int) -> int:
    try: return int(os.getenv(key, str(default)))
    except ValueError: return default

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID", "8569699093").strip()
DOWNLOAD_API_URL = os.getenv("DOWNLOAD_API_URL", "https://gfdbgta.pythonanywhere.com/generate_link").strip()
DOWNLOAD_API_SECRET = os.getenv("DOWNLOAD_API_SECRET", "").strip()
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://instagram.com/p1ay.zone").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DB_FILE = DATA_DIR / "playzone_bot_data.json"

PAYMENT_TIMEOUT_SECONDS = get_env_int("PAYMENT_TIMEOUT_SECONDS", 60 * 60 * 24)
SESSION_TIMEOUT_SECONDS = get_env_int("SESSION_TIMEOUT_SECONDS", 60 * 60 * 2)
RATE_LIMIT_SECONDS = get_env_int("RATE_LIMIT_SECONDS", 3)
HTTP_TIMEOUT_SECONDS = get_env_int("HTTP_TIMEOUT_SECONDS", 15)

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
except ValueError as exc:
    raise RuntimeError("ADMIN_CHAT_ID must be a valid integer") from exc

def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_CHAT_ID)

# =====================================================
# Web Server (Keep-Alive Thread)
# =====================================================
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ PlayZone Bot is ONLINE and functioning flawlessly!"

def run_web_server():
    port = get_env_int("PORT", 8080)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    thread = Thread(target=run_web_server, daemon=True)
    thread.start()

# =====================================================
# Database Architecture
# =====================================================
DEVICES = {"android": "📱 Android", "ios": "🍎 iPhone"}
STATUS_LABELS = {
    "pending": "⏳ قيد المراجعة",
    "approved": "✅ مقبول",
    "rejected": "❌ مرفوض",
    "expired": "⌛ منتهي",
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
    "vouchers": {"FREE-PLAYZONE": {"game_id": "thechallenge", "is_active": True, "uses_left": 5}},
    "pending_payments": {},
    "sessions": {},
    "orders": {},
    "users": {},
    "rate_limits": {},
    "stats": {
        "started_users": 0,
        "submitted_receipts": 0,
        "approved_orders": 0,
        "rejected_orders": 0,
        "generated_links": 0,
        "order_counter": 1000,
    },
}

_db_lock = asyncio.Lock()
db: Dict[str, Any] = json.loads(json.dumps(DEFAULT_DB))

# --- Sync & Async DB Helpers ---
def utc_now_ts() -> int: return int(time.time())
def iso_now() -> str: return datetime.now(timezone.utc).isoformat()
def now_text() -> str: return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def user_key(user_id: int) -> str: return str(user_id)
def escape_text(value: Any) -> str: return html.escape(str(value or ""))
def get_setting(key: str) -> Any: return db.get("settings", {}).get(key, DEFAULT_DB["settings"].get(key))
def get_game_title(game_id: str) -> str: return db.get("games", {}).get(game_id, {}).get("title", game_id)
def safe_int(value: Any, default: int = 0) -> int:
    try: return int(value)
    except Exception: return default

def merge_defaults(base: Dict, loaded: Dict) -> Dict:
    merged = json.loads(json.dumps(base))
    for k, v in loaded.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged

def load_db_sync() -> None:
    global db
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists(): return
    try:
        with DB_FILE.open("r", encoding="utf-8") as f:
            db = merge_defaults(DEFAULT_DB, json.load(f))
    except Exception as e:
        logger.error(f"DB Load Error: {e}")

def save_db_sync() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_FILE.with_suffix(".tmp")
    try:
        if DB_FILE.exists(): DB_FILE.replace(DB_FILE.with_suffix(".bak"))
    except: pass
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    tmp.replace(DB_FILE)

async def update_db(mutator) -> Any:
    async with _db_lock:
        res = mutator(db)
        save_db_sync()
        return res

async def is_rate_limited(user_id: int, action: str) -> bool:
    if is_admin(user_id): return False
    now = utc_now_ts()
    key = f"{user_id}:{action}"
    def mutate(data):
        last = int(data.setdefault("rate_limits", {}).get(key, 0))
        if now - last < RATE_LIMIT_SECONDS: return True
        data["rate_limits"][key] = now
        return False
    return await update_db(mutate)

async def next_order_id() -> str:
    def mutate(data):
        cnt = int(data.setdefault("stats", {}).get("order_counter", 1000)) + 1
        data["stats"]["order_counter"] = cnt
        return f"PZ-{cnt}"
    return await update_db(mutate)

def get_session(user_id: int) -> Dict:
    return dict(db.get("sessions", {}).get(user_key(user_id), {}))

async def upsert_session(user, changes: Dict, push_screen: Optional[str] = None) -> None:
    def mutate(data):
        sess = data.setdefault("sessions", {}).setdefault(user_key(user.id), {"user_id": user.id, "history": []})
        sess.update({"full_name": user.full_name, "username": user.username or "", "updated_ts": utc_now_ts()})
        sess.update(changes)
        if push_screen:
            hist = sess.setdefault("history", [])
            if not hist or hist[-1] != push_screen:
                hist.append(push_screen)
                sess["history"] = hist[-8:]
    await update_db(mutate)

def get_user_orders(user_id: int) -> List[Dict]:
    orders = [o for o in list(db.get("orders", {}).values()) if str(o.get("user_id")) == str(user_id)]
    orders.sort(key=lambda x: int(x.get("created_ts", 0)), reverse=True)
    return orders

def is_user_banned(user_id: int) -> bool:
    return str(user_id) in db.get("banned_users", [])

# =====================================================
# Keyboards & UI
# =====================================================
def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup: return InlineKeyboardMarkup(rows)

def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎮 الألعاب", callback_data="menu:games"), InlineKeyboardButton("📦 حالة طلبي", callback_data="menu:status")],
        [InlineKeyboardButton("🕹️ ألعابي", callback_data="menu:my_games"), InlineKeyboardButton("💳 الدفع", callback_data="menu:payment")],
        [InlineKeyboardButton("❓ المساعدة", callback_data="menu:help"), InlineKeyboardButton("📞 الدعم الفني", callback_data="menu:support")],
        [InlineKeyboardButton("🎁 استرداد كود هدية", callback_data="menu:redeem_voucher")]
    ]
    if is_admin(user_id): rows.append([InlineKeyboardButton("👑 لوحة تحكم الإدارة", callback_data="admin:panel")])
    return kb(rows)

def games_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{g.get('emoji','🎮')} {g.get('title','لعبة')}", callback_data=f"game:{k}")] for k, g in db.get("games", {}).items()]
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")])
    return kb(rows)

def devices_keyboard(game_id: str) -> InlineKeyboardMarkup:
    game = db.get("games", {}).get(game_id, {})
    rows = [[InlineKeyboardButton(DEVICES.get(d, d), callback_data=f"device:{game_id}:{d}")] for d in game.get("available_devices", [])]
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
        [InlineKeyboardButton("📸 طريقة إرسال الإيصال", callback_data="pay:receipt_help")],
        [InlineKeyboardButton("🎮 اختيار لعبة أخرى", callback_data="menu:games")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")],
    ])

# --- Admin UI ---
def admin_panel_keyboard() -> InlineKeyboardMarkup:
    maint_txt = "🔴 إيقاف الصيانة" if get_setting("maintenance_mode") else "🟢 تشغيل الصيانة"
    return kb([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats"), InlineKeyboardButton("⏳ الطلبات المعلقة", callback_data="admin:pending")],
        [InlineKeyboardButton("🎮 إدارة الألعاب", callback_data="admin:manage_games"), InlineKeyboardButton("📢 الإذاعة", callback_data="admin:smart_broadcast")],
        [InlineKeyboardButton("⚙️ إعدادات البوت", callback_data="admin:settings"), InlineKeyboardButton(maint_txt, callback_data="admin:toggle_maintenance")],
        [InlineKeyboardButton("📤 أخذ نسخة احتياطية", callback_data="admin:export")],
        [InlineKeyboardButton("🏠 إغلاق اللوحة", callback_data="menu:home")],
    ])

def manage_games_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("➕ إضافة لعبة جديدة", callback_data="admin:add_game_start")],
        [InlineKeyboardButton("🗑️ حذف لعبة", callback_data="admin:delete_game_list")],
        [InlineKeyboardButton("⬅️ رجوع للوحة", callback_data="admin:panel")]
    ])

def delete_games_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"❌ حذف {g.get('title', k)}", callback_data=f"admin:del_game:{k}")] for k, g in db.get("games", {}).items()]
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="admin:manage_games")])
    return kb(rows)

def admin_review_keyboard(order_id: str, user_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("✅ قبول (تجهيز زر التحميل)", callback_data=f"admin:approve:{order_id}")],
        [InlineKeyboardButton("❌ رفض الإيصال", callback_data=f"admin:reject_menu:{order_id}"), InlineKeyboardButton("ℹ️ معلومات", callback_data=f"admin:info:{order_id}")],
        [InlineKeyboardButton("🚫 حظر المستخدم", callback_data=f"admin:ban:{user_id}"), InlineKeyboardButton("👑 العودة", callback_data="admin:panel")],
    ])

def rejection_reasons_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("❌ الإيصال غير واضح", callback_data=f"admin:reject_reason:{order_id}:unclear")],
        [InlineKeyboardButton("❌ المبلغ غير صحيح", callback_data=f"admin:reject_reason:{order_id}:wrong_amount")],
        [InlineKeyboardButton("❌ لم يصل التحويل", callback_data=f"admin:reject_reason:{order_id}:not_received")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data=f"admin:back:{order_id}")],
    ])

# --- Protected Downloads ---
def protected_download_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("⬇️ اضغط لتوليد الرابط الآمن الخاص بك", callback_data=f"download:reveal:{order_id}")],
        [InlineKeyboardButton("📲 طريقة التثبيت", callback_data=f"download:install:{order_id}"), InlineKeyboardButton("📞 الدعم الفني", callback_data=f"download:support:{order_id}")]
    ])

def actual_download_keyboard(download_url: str, order_id: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton("✅ تحميل اللعبة الآن", url=download_url)],
        [InlineKeyboardButton("📲 طريقة التثبيت", callback_data=f"download:install:{order_id}")]
    ])

# =====================================================
# API / Secure Link Generation
# =====================================================
async def generate_download_link(user_id: int, game_id: str, device_code: str, order_id: str) -> Optional[str]:
    payload = {"user_id": str(user_id), "device": device_code, "game": game_id.lower(), "order_id": order_id, "timestamp": utc_now_ts()}
    if DOWNLOAD_API_SECRET:
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["signature"] = hmac.new(DOWNLOAD_API_SECRET.encode("utf-8"), msg, "sha256").hexdigest()
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)) as sess:
            async with sess.post(DOWNLOAD_API_URL, json=payload) as resp:
                if resp.status < 400: return (await resp.json()).get("download_url")
    except Exception as e: logger.error(f"API Error: {e}")
    return None

# =====================================================
# Background Jobs & Tasks
# =====================================================
async def auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    if not DB_FILE.exists(): return
    try:
        with DB_FILE.open("rb") as f:
            await context.bot.send_document(chat_id=ADMIN_CHAT_ID, document=f, filename=f"PlayZone_Backup_{iso_now()[:10]}.json", caption="🔄 نسخة احتياطية تلقائية.")
    except Exception as e: logger.error(f"Auto-backup failed: {e}")

async def broadcast_task(bot, target_group: str, text: str, admin_msg_id: int, chat_id: int):
    users_list = list(db.get("users", {}).keys())
    buyers_set = set(str(o.get("user_id")) for o in db.get("orders", {}).values() if o.get("status") == "approved")
    
    count, failed = 0, 0
    for uid in users_list:
        is_buyer = uid in buyers_set
        if target_group == "buyers" and not is_buyer: continue
        if target_group == "no_buyers" and is_buyer: continue
        try:
            await bot.send_message(chat_id=int(uid), text=f"📢 <b>إعلان:</b>\n\n{text}", parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.05) # Prevent Flood
        except Exception: failed += 1
            
    try:
        await bot.send_message(chat_id=chat_id, text=f"✅ انتهت الإذاعة!\nوصلت لـ {count} مستخدم.\nفشلت لـ {failed} مستخدم.", reply_markup=admin_panel_keyboard())
    except Exception: pass

# =====================================================
# Handlers (Robust & Safe)
# =====================================================
def clear_all_admin_states(session: Dict) -> Dict:
    # Clear any pending inputs to prevent state collisions
    keys_to_clear = ["adding_game_step", "new_game_data", "awaiting_broadcast", "broadcast_target", "replying_to_user", "reply_target_id", "awaiting_support_msg", "awaiting_voucher", "awaiting_new_card", "awaiting_new_price"]
    for k in keys_to_clear: session.pop(k, None)
    return session

async def check_maintenance(update: Update) -> bool:
    if get_setting("maintenance_mode") and not is_admin(update.effective_user.id):
        txt = "🛠️ <b>عذراً!</b>\nالبوت حالياً في وضع الصيانة للتحديثات. نعود لكم بعد قليل ⏳"
        if update.message: await update.message.reply_text(txt, parse_mode="HTML")
        elif update.callback_query: 
            try: await update.callback_query.answer("🛠️ البوت في وضع الصيانة.", show_alert=True)
            except: pass
        return True
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_maintenance(update): return
    user = update.effective_user
    if is_user_banned(user.id): return

    def reg(data):
        if user_key(user.id) not in data.setdefault("users", {}):
            data["users"][user_key(user.id)] = {"user_id": user.id, "first_seen": iso_now()}
            data.setdefault("stats", {})["started_users"] = int(data.get("stats", {}).get("started_users", 0)) + 1
    await update_db(reg)
    await upsert_session(user, clear_all_admin_states({}), push_screen="home")

    text = f"👋 <b>أهلاً بك في PlayZone</b>\n\nمتجرك الأول والآمن لتحميل الألعاب.\nاختر من القائمة لتبدأ:"
    if is_admin(user.id): text += "\n\n👑 <b>مرحباً أيها المدير. وضع التحكم مفعل.</b>"
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard(user.id))

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_session(user, clear_all_admin_states({}))
    await update.message.reply_text("✅ تم إلغاء أي عمليات قيد التنفيذ والعودة للوضع الطبيعي.", reply_markup=main_menu_keyboard(user.id))

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    target = context.args[0]
    await update_db(lambda d: d.get("banned_users", []).remove(target) if target in d.get("banned_users", []) else None)
    await update.message.reply_text(f"✅ تم فك الحظر عن: {target}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_maintenance(update): return
    user = update.message.from_user
    if is_user_banned(user.id): return
    
    text = update.message.text.strip()
    session = get_session(user.id)

    # 1. Admin Add Game
    if is_admin(user.id) and session.get("adding_game_step"):
        step = session["adding_game_step"]
        game_data = dict(session.get("new_game_data", {}))
        
        if step == "id":
            gid = text.lower().replace(" ", "")
            if len(gid) < 2: return await update.message.reply_text("⚠️ المعرف قصير جداً. اكتب معرفاً صالحاً:")
            game_data["id"] = gid
            await upsert_session(user, {"adding_game_step": "title", "new_game_data": game_data})
            return await update.message.reply_text("✅ ممتاز.\nأرسل <b>اسم اللعبة</b> كما سيظهر للمستخدمين:", parse_mode="HTML")
            
        elif step == "title":
            game_data["title"] = text
            await upsert_session(user, {"adding_game_step": "emoji", "new_game_data": game_data})
            return await update.message.reply_text("✅ رائع.\nأرسل <b>إيموجي</b> يعبر عن اللعبة (مثال: 🚗):", parse_mode="HTML")
            
        elif step == "emoji":
            game_data["emoji"] = text
            await upsert_session(user, {"adding_game_step": "desc", "new_game_data": game_data})
            return await update.message.reply_text("✅ تم.\nأرسل <b>وصف اللعبة</b>:", parse_mode="HTML")
            
        elif step == "desc":
            game_data["description"] = text
            await upsert_session(user, {"adding_game_step": "devices", "new_game_data": game_data})
            return await update.message.reply_text("✅ خطوة أخيرة.\nاكتب الأجهزة المدعومة مفصولة بفاصلة (مثال: <code>android, ios</code>):", parse_mode="HTML")
            
        elif step == "devices":
            devs = [d.strip() for d in text.lower().split(",") if d.strip() in ["android", "ios"]]
            game_id = game_data.pop("id", None)
            if not game_id:
                await upsert_session(user, clear_all_admin_states({}))
                return await update.message.reply_text("⚠️ حدث خطأ داخلي. أعد المحاولة.")
            game_data["available_devices"] = devs or ["android"]
            
            await update_db(lambda d: d.setdefault("games", {}).update({game_id: game_data}))
            await upsert_session(user, clear_all_admin_states({}))
            return await update.message.reply_text(f"🎉 <b>تمت إضافة اللعبة بنجاح!</b>\n\n🎮 {game_data.get('emoji','')} {game_data.get('title','')}", parse_mode="HTML", reply_markup=admin_panel_keyboard())

    # 2. Admin Settings
    if is_admin(user.id):
        if session.get("awaiting_new_card"):
            await update_db(lambda d: d.setdefault("settings", {}).update({"payment_card": text}))
            await upsert_session(user, clear_all_admin_states({}))
            return await update.message.reply_text(f"✅ تم تحديث البطاقة إلى: <code>{escape_text(text)}</code>", parse_mode="HTML", reply_markup=admin_panel_keyboard())
        if session.get("awaiting_new_price"):
            await update_db(lambda d: d.setdefault("settings", {}).update({"game_price": text}))
            await upsert_session(user, clear_all_admin_states({}))
            return await update.message.reply_text(f"✅ تم تحديث السعر إلى: <b>{escape_text(text)}</b>", parse_mode="HTML", reply_markup=admin_panel_keyboard())

    # 3. Admin Ticket Reply
    if is_admin(user.id) and session.get("replying_to_user"):
        target = session.get("reply_target_id")
        try:
            await context.bot.send_message(chat_id=target, text=f"👨‍💻 <b>رد من الإدارة:</b>\n\n{escape_text(text)}", parse_mode="HTML")
            await update.message.reply_text("✅ تم إرسال الرد.", reply_markup=admin_panel_keyboard())
        except: await update.message.reply_text("⚠️ فشل الإرسال (المستخدم قام بحظر البوت).")
        await upsert_session(user, clear_all_admin_states({}))
        return

    # 4. Admin Broadcast (Non-Blocking Task)
    if is_admin(user.id) and session.get("awaiting_broadcast"):
        target_group = session.get("broadcast_target", "all")
        await upsert_session(user, clear_all_admin_states({}))
        msg = await update.message.reply_text("⏳ جاري بدء الإذاعة في الخلفية...")
        asyncio.create_task(broadcast_task(context.bot, target_group, text, msg.message_id, user.id))
        return

    # 5. User Ticket
    if session.get("awaiting_support_msg"):
        order_count = len(get_user_orders(user.id))
        alert = f"📩 <b>رسالة دعم</b>\n👤 العميل: {escape_text(user.full_name)} | <code>{user.id}</code>\n🛍️ طلباته: {order_count}\n\n💬 الرسالة:\n{escape_text(text)}"
        await context.bot.send_message(ADMIN_CHAT_ID, alert, parse_mode="HTML", reply_markup=kb([[InlineKeyboardButton("📝 رد مباشر", callback_data=f"admin:reply_ticket:{user.id}")], [InlineKeyboardButton("🚫 حظر", callback_data=f"admin:ban:{user.id}")]]))
        await upsert_session(user, {"awaiting_support_msg": False})
        return await update.message.reply_text("✅ تم إرسال رسالتك للإدارة، سيصلك الرد هنا.", reply_markup=main_menu_keyboard(user.id))

    # 6. User Voucher
    if session.get("awaiting_voucher"):
        code = text.upper()
        vouchers = db.get("vouchers", {})
        if code in vouchers and vouchers[code].get("is_active", True) and vouchers[code].get("uses_left", 0) > 0:
            gid, oid = vouchers[code]["game_id"], await next_order_id()
            def apply_voucher(data):
                data["vouchers"][code]["uses_left"] -= 1
                if data["vouchers"][code]["uses_left"] <= 0: data["vouchers"][code]["is_active"] = False
                data.setdefault("orders", {})[oid] = {"order_id": oid, "game": gid, "device": "android", "user_id": user.id, "status": "approved", "price": "0 (كود هدية)", "created_ts": utc_now_ts()}
            await update_db(apply_voucher)
            await upsert_session(user, {"awaiting_voucher": False})
            return await update.message.reply_text("🎉 <b>مبروك!</b> الكود صحيح وتم شحن اللعبة.\nاذهب إلى <b>(ألعابي)</b> للتحميل.", parse_mode="HTML", reply_markup=kb([[InlineKeyboardButton("🕹️ ألعابي", callback_data="menu:my_games")]]))
        return await update.message.reply_text("❌ الكود غير صحيح أو مستخدم مسبقاً.")

    await update.message.reply_text("استخدم الأزرار للتنقل أو اتبع التعليمات على الشاشة.", reply_markup=main_menu_keyboard(user.id))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_maintenance(update): return
    user = update.message.from_user
    if is_user_banned(user.id) or is_admin(user.id): return
    
    if await is_rate_limited(user.id, "photo"):
        return await update.message.reply_text("⏳ نرجو الانتظار قليلاً قبل إرسال صورة أخرى.")

    sess = get_session(user.id)
    gid, dev = sess.get("game"), sess.get("device")
    if not gid or not dev:
        return await update.message.reply_text("⚠️ اختر اللعبة ونوع الجهاز من القائمة أولاً، ثم أرسل الإيصال.", reply_markup=games_keyboard())

    if any(str(o.get("user_id")) == str(user.id) for o in db.get("pending_payments", {}).values()):
        return await update.message.reply_text("⏳ لديك طلب قيد المراجعة. انتظر حتى نراجع الأول.")

    oid = await next_order_id()
    order = {"order_id": oid, "file_id": update.message.photo[-1].file_id, "game": gid, "device": dev, "user_id": user.id, "full_name": user.full_name, "username": user.username or "", "status": "pending", "price": get_setting("game_price"), "created_ts": utc_now_ts(), "created_at_text": now_text()}
    
    def mutate(data):
        data.setdefault("pending_payments", {})[oid] = order
        data.setdefault("orders", {})[oid] = order.copy()
        data.setdefault("stats", {})["submitted_receipts"] = int(data.get("stats", {}).get("submitted_receipts", 0)) + 1
    await update_db(mutate)

    caption = f"📩 <b>إيصال جديد</b>\n🧾 <code>{oid}</code>\n👤 {escape_text(user.full_name)} | <code>{user.id}</code>\n🎮 {escape_text(get_game_title(gid))}\n📱 {escape_text(DEVICES.get(dev, dev))}\n💰 {get_setting('game_price')}"
    try: await context.bot.send_photo(ADMIN_CHAT_ID, order["file_id"], caption=caption, parse_mode="HTML", reply_markup=admin_review_keyboard(oid, str(user.id)))
    except Exception as e: logger.error(f"Receipt forward error: {e}")
    
    await update.message.reply_text(f"✅ <b>تم استلام الإيصال</b>\n🧾 رقم: <code>{oid}</code>\nجاري المراجعة، سيصلك زر التحميل قريباً.", parse_mode="HTML", reply_markup=kb([[InlineKeyboardButton("📦 حالة الطلب", callback_data="menu:status")]]))

# =====================================================
# Callbacks (Strictly Safe)
# =====================================================
async def edit_msg(query, text: str, markup: InlineKeyboardMarkup):
    try: await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            try: await query.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
            except: pass
    except: pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_maintenance(update): return
    if is_user_banned(query.from_user.id):
        try: await query.answer("❌ حسابك محظور.", show_alert=True)
        except: pass
        return
        
    parts = query.data.split(":")
    prefix, action = parts[0], parts[1] if len(parts) > 1 else ""
    
    try:
        try: await query.answer()
        except: pass
        
        if prefix == "menu": await handle_menu(query, action)
        elif prefix == "nav": await handle_nav(query)
        elif prefix == "game":
            await upsert_session(query.from_user, clear_all_admin_states({"game": action}), push_screen="game")
            g = db.get("games", {}).get(action, {})
            await edit_msg(query, f"<b>{escape_text(g.get('title', action))}</b>\n\n{escape_text(g.get('description', ''))}\n\nاختر نوع جهازك:", devices_keyboard(action))
        elif prefix == "device":
            dev = parts[2] if len(parts) > 2 else "android"
            await upsert_session(query.from_user, {"game": action, "device": dev}, push_screen="payment")
            txt = f"💳 <b>إتمام الدفع</b>\n\n🎮 اللعبة: {escape_text(get_game_title(action))}\n📱 الجهاز: {DEVICES.get(dev, dev)}\n💰 السعر: <b>{get_setting('game_price')}</b>\n\nحوّل المبلغ לـ ماستر كارد:\n<code>{get_setting('payment_card')}</code>\n\n📸 <b>ثم أرسل صورة الإيصال هنا.</b>"
            await edit_msg(query, txt, payment_keyboard())
        elif prefix == "download": await handle_download(query, context, parts)
        elif prefix == "admin": await handle_admin(query, context, parts)
        elif prefix == "pay" and action == "receipt_help":
            await edit_msg(query, "📸 <b>طريقة الإرسال:</b>\nصوّر حوالتك من تطبيق المحفظة، واضغط (📎) لإرسالها كصورة هنا.", payment_keyboard())
    except Exception as e:
        logger.error(f"Callback error: {e}")

async def handle_menu(query, screen: str):
    user = query.from_user
    if screen == "home":
        await upsert_session(user, {"history": ["home"]})
        await edit_msg(query, "👋 <b>أهلاً بك في القائمة الرئيسية</b>\nاختر ما يناسبك:", main_menu_keyboard(user.id))
    elif screen == "games":
        await upsert_session(user, clear_all_admin_states({}), push_screen="games")
        if not db.get("games", {}): return await edit_msg(query, "🎮 <b>لا توجد ألعاب متاحة حالياً.</b>", kb([[InlineKeyboardButton("⬅️ رجوع", callback_data="nav:back")]]))
        await edit_msg(query, "🎮 <b>الألعاب المتوفرة</b>\nاختر اللعبة:", games_keyboard())
    elif screen == "status":
        latest = get_user_orders(user.id)
        if not latest: return await edit_msg(query, "لا يوجد لديك طلبات سابقة.", kb([[InlineKeyboardButton("🎮 تصفح الألعاب", callback_data="menu:games")]]))
        o = latest[0]
        status = STATUS_LABELS.get(o.get("status", "pending"), "مجهول")
        txt = f"📦 <b>أحدث طلب لك</b>\n🧾 <code>{o['order_id']}</code>\n🎮 {get_game_title(o['game'])}\n📌 الحالة: <b>{status}</b>"
        await edit_msg(query, txt, protected_download_keyboard(o["order_id"]) if o.get("status") == "approved" else kb([[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]))
    elif screen == "my_games":
        orders = [o for o in get_user_orders(user.id) if o.get("status") == "approved"]
        if not orders: return await edit_msg(query, "لم تقم بشراء أي ألعاب بعد.", kb([[InlineKeyboardButton("🎮 تصفح الألعاب", callback_data="menu:games")]]))
        lines = ["🕹️ <b>ألعابك المشتراة</b>\n"] + [f"• {get_game_title(o['game'])} (<code>{o['order_id']}</code>)" for o in orders[:10]] + ["\nلتحميل لعبة، اذهب لحالة الطلب."]
        await edit_msg(query, "\n".join(lines), kb([[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]))
    elif screen == "payment":
        sess = get_session(user.id)
        if not sess.get("game") or not sess.get("device"): return await edit_msg(query, "⚠️ <b>اختر اللعبة أولاً!</b>", kb([[InlineKeyboardButton("🎮 تصفح", callback_data="menu:games")], [InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]))
        txt = f"💳 <b>إتمام الدفع</b>\n\n🎮 {escape_text(get_game_title(sess['game']))}\n📱 {DEVICES.get(sess['device'], sess['device'])}\n💰 <b>{get_setting('game_price')}</b>\n\nماستر كارد: <code>{get_setting('payment_card')}</code>\n\n📸 <b>ثم أرسل الإيصال.</b>"
        await edit_msg(query, txt, payment_keyboard())
    elif screen == "help":
        await edit_msg(query, "❓ <b>كيف تشتري؟</b>\n1️⃣ اختر اللعبة والجهاز.\n2️⃣ حوّل المبلغ للبطاقة.\n3️⃣ أرسل الإيصال كصورة.\n4️⃣ بعد الموافقة، يصلك الرابط.", kb([[InlineKeyboardButton("📞 الدعم", callback_data="menu:support")], [InlineKeyboardButton("⬅️ رجوع", callback_data="menu:home")]]))
    elif screen == "contact_admin":
        await upsert_session(user, clear_all_admin_states({"awaiting_support_msg": True}), push_screen="contact_admin")
        await edit_msg(query, "✉️ <b>مراسلة الإدارة</b>\nاكتب مشكلتك في رسالة واحدة الآن:", kb([[InlineKeyboardButton("❌ إلغاء", callback_data="menu:home")]]))
    elif screen == "support":
        await upsert_session(user, clear_all_admin_states({}), push_screen="support")
        await edit_msg(query, "📞 <b>مركز الدعم الفني</b>", support_keyboard())
    elif screen == "redeem_voucher":
        await upsert_session(user, clear_all_admin_states({"awaiting_voucher": True}), push_screen="redeem_voucher")
        await edit_msg(query, "🎁 <b>كود هدية</b>\nأرسل الكود الآن:", kb([[InlineKeyboardButton("❌ إلغاء", callback_data="menu:home")]]))

async def handle_nav(query):
    sess = get_session(query.from_user.id)
    hist = sess.get("history", []) or ["home"]
    if len(hist) > 1: hist.pop()
    await update_db(lambda d: d.setdefault("sessions", {}).setdefault(user_key(query.from_user.id), {}).update({"history": hist}))
    await handle_menu(query, hist[-1] if hist else "home")

async def handle_download(query, context, parts):
    action, oid = (parts[1], parts[2]) if len(parts) > 2 else ("", "")
    order = db.get("orders", {}).get(oid)
    
    if not order:
        try: await query.answer("لم أجد الطلب!", show_alert=True)
        except: pass
        return
    if str(order.get("user_id")) != str(query.from_user.id) and not is_admin(query.from_user.id):
        try: await query.answer("❌ الرابط محمي لمشتري اللعبة فقط!", show_alert=True)
        except: pass
        return

    if action == "reveal":
        try: await query.answer("⏳ جاري توليد رابط مشفر...", show_alert=False)
        except: pass
        url = await generate_download_link(order["user_id"], order["game"], order.get("device","android"), oid)
        if not url:
            try: await query.answer("⚠️ فشل الاتصال. جرب لاحقاً.", show_alert=True)
            except: pass
            return
        await edit_msg(query, "✅ <b>تم التوليد!</b>\n⚠️ الرابط خاص بجهازك وسينتهي بعد 10 دقائق.", actual_download_keyboard(url, oid))
    elif action == "install":
        txt = "📲 <b>التثبيت:</b>\n1️⃣ اضغط التحميل\n2️⃣ افتح ملف APK\n3️⃣ وافق على 'التثبيت من مصادر غير معروفة'." if order.get("device") == "android" else "🍎 <b>الآيفون:</b>\nالتثبيت من خارج المتجر يحتاج شهادات. راسل الدعم."
        await edit_msg(query, txt, kb([[InlineKeyboardButton("⬅️ رجوع", callback_data=f"menu:status")]]))
    elif action == "support":
        await edit_msg(query, "📞 <b>الدعم الفني:</b>", support_keyboard())

# --- Admin Core ---
async def handle_admin(query, context, parts):
    if not is_admin(query.from_user.id): return
    action = parts[1] if len(parts) > 1 else ""
    
    if action == "panel":
        await upsert_session(query.from_user, clear_all_admin_states({}))
        await edit_msg(query, "👑 <b>لوحة تحكم النظام (CRM)</b>\nالتحكم الكامل:", admin_panel_keyboard())
    elif action == "stats":
        stats = db.get("stats", {})
        txt = f"📊 <b>إحصائيات</b>\n\n👥 مستخدمين: <b>{len(db.get('users', {}))}</b>\n🧾 طلبات: <b>{len(db.get('orders', {}))}</b>\n⏳ معلقة: <b>{len(db.get('pending_payments', {}))}</b>\n✅ مقبولة: <b>{stats.get('approved_orders', 0)}</b>\n❌ مرفوضة: <b>{stats.get('rejected_orders', 0)}</b>"
        await edit_msg(query, txt, kb([[InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel")]]))
    elif action == "settings":
        await edit_msg(query, "⚙️ <b>إعدادات البوت</b>", kb([[InlineKeyboardButton("💳 تغيير بطاقة الدفع", callback_data="admin:change_card")], [InlineKeyboardButton("💰 تغيير السعر", callback_data="admin:change_price")], [InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel")]]))
    elif action == "change_card":
        await upsert_session(query.from_user, clear_all_admin_states({"awaiting_new_card": True}))
        await edit_msg(query, "💳 أرسل رقم البطاقة الجديد:\n(للإلغاء اكتب /cancel)", kb([]))
    elif action == "change_price":
        await upsert_session(query.from_user, clear_all_admin_states({"awaiting_new_price": True}))
        await edit_msg(query, "💰 أرسل السعر الجديد (مثال 1000 IQD):\n(للإلغاء اكتب /cancel)", kb([]))
    elif action == "manage_games":
        await edit_msg(query, "🎮 <b>إدارة الألعاب</b>", manage_games_keyboard())
    elif action == "add_game_start":
        await upsert_session(query.from_user, clear_all_admin_states({"adding_game_step": "id", "new_game_data": {}}))
        await edit_msg(query, "➕ <b>إضافة لعبة (الخطوة 1 من 5)</b>\nأرسل المعرف البرمجي (مثال: gta6):", kb([]))
    elif action == "delete_game_list":
        await edit_msg(query, "🗑️ <b>حذف لعبة</b>", delete_games_keyboard())
    elif action == "del_game" and len(parts) > 2:
        await update_db(lambda d: d.setdefault("games", {}).pop(parts[2], None))
        try: await query.answer("✅ تم الحذف!", show_alert=True)
        except: pass
        await edit_msg(query, "🗑️ تم التحديث:", delete_games_keyboard())
    elif action == "toggle_maintenance":
        await update_db(lambda d: d.setdefault("settings", {}).update({"maintenance_mode": not get_setting("maintenance_mode")}))
        await edit_msg(query, "👑 <b>لوحة تحكم النظام (CRM)</b>", admin_panel_keyboard())
    elif action == "pending":
        pending = sorted(list(db.get("pending_payments", {}).values()), key=lambda x: int(x.get("created_ts", 0)))
        if not pending: return await edit_msg(query, "✅ لا يوجد طلبات معلقة!", admin_panel_keyboard())
        o = pending[0]
        if o.get("file_id"):
            await context.bot.send_photo(ADMIN_CHAT_ID, o["file_id"], caption=f"⚠️ <b>يوجد {len(pending)} طلبات!</b>\n\nرقم: <code>{o['order_id']}</code>\nاللعبة: {get_game_title(o['game'])}", parse_mode="HTML", reply_markup=admin_review_keyboard(o["order_id"], str(o.get("user_id", ""))))
            try: await query.message.delete()
            except: pass
    elif action == "smart_broadcast":
        await edit_msg(query, "📢 <b>الإذاعة الموجهة</b>\nاختر الفئة المستهدفة:", kb([[InlineKeyboardButton("🌍 للجميع", callback_data="admin:broadcast:all")], [InlineKeyboardButton("💳 المشترين", callback_data="admin:broadcast:buyers")], [InlineKeyboardButton("👀 المترددين", callback_data="admin:broadcast:no_buyers")], [InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel")]]))
    elif action == "broadcast" and len(parts) > 2:
        await upsert_session(query.from_user, clear_all_admin_states({"awaiting_broadcast": True, "broadcast_target": parts[2]}))
        await edit_msg(query, f"📢 أرسل الإعلان الآن ({parts[2]}):", kb([]))
    elif action == "export":
        exp = DATA_DIR / f"DB_Backup_{int(time.time())}.json"
        with exp.open("w", encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False, indent=2)
        await context.bot.send_document(ADMIN_CHAT_ID, exp.open("rb"), caption="📤 النسخة الاحتياطية الحالية.")
    elif action == "ban" and len(parts) > 2:
        def ban_usr(data):
            if parts[2] not in data.setdefault("banned_users", []): data["banned_users"].append(parts[2])
        await update_db(ban_usr)
        try: await query.answer("تم الحظر!", show_alert=True)
        except: pass
    elif action in ["approve", "reject_menu", "reject_reason", "info", "back", "reply_ticket"]:
        if action == "reply_ticket" and len(parts) > 2:
            await upsert_session(query.from_user, clear_all_admin_states({"replying_to_user": True, "reply_target_id": parts[2]}))
            return await edit_msg(query, f"📝 <b>الرد على <code>{parts[2]}</code></b>\nأرسل رسالتك الآن:", kb([[InlineKeyboardButton("❌ إلغاء", callback_data="admin:panel")]]))
        oid = parts[2] if len(parts) > 2 else ""
        order = db.get("pending_payments", {}).get(oid)
        if not order and action != "info":
            try: await query.answer("تمت المعالجة مسبقاً!", show_alert=True)
            except: pass
            return
        if action == "approve":
            def mut_app(data):
                if d_app := data.setdefault("pending_payments", {}).pop(oid, None):
                    d_app["status"] = "approved"
                    data.setdefault("orders", {})[oid] = d_app
                    data.setdefault("stats", {})["approved_orders"] = int(data.get("stats", {}).get("approved_orders", 0)) + 1
            await update_db(mut_app)
            try: await context.bot.send_message(order["user_id"], f"✅ <b>تم قبول إيصال الدفع!</b>\n\n🎮 اللعبة: {get_game_title(order['game'])}\n🧾 الطلب: <code>{oid}</code>\n\nاضغط لتوليد الرابط الآمن:", parse_mode="HTML", reply_markup=protected_download_keyboard(oid))
            except: pass
            try: await query.edit_message_caption(caption=f"✅ تمت الموافقة على الطلب <code>{oid}</code>.", parse_mode="HTML")
            except: pass
        elif action == "reject_menu": await query.edit_message_reply_markup(reply_markup=rejection_reasons_keyboard(oid))
        elif action == "back": await query.edit_message_reply_markup(reply_markup=admin_review_keyboard(oid, str(order.get("user_id", ""))))
        elif action == "reject_reason" and len(parts) > 3:
            rsn = REJECTION_REASONS.get(parts[3], "غير مطابق")
            def mut_rej(data):
                if d_rej := data.setdefault("pending_payments", {}).pop(oid, None):
                    d_rej["status"], d_rej["rejection_reason"] = "rejected", rsn
                    data.setdefault("orders", {})[oid] = d_rej
                    data.setdefault("stats", {})["rejected_orders"] = int(data.get("stats", {}).get("rejected_orders", 0)) + 1
            await update_db(mut_rej)
            try: await context.bot.send_message(order["user_id"], f"❌ <b>تم رفض الإيصال</b>\nالسبب: {rsn}\n\nتواصل مع الدعم.", parse_mode="HTML", reply_markup=support_keyboard())
            except: pass
            try: await query.edit_message_caption(caption=f"❌ تم الرفض: {rsn}", parse_mode="HTML")
            except: pass
        elif action == "info":
            o = order or db.get("orders", {}).get(oid)
            if o:
                try: await query.answer(f"Client: {o.get('full_name')}\nUser ID: {o.get('user_id')}", show_alert=True)
                except: pass

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception handled:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try: await update.effective_message.reply_text("⚠️ حدث خطأ داخلي. تم تبليغ الإدارة وتجاوز الخطأ.")
        except: pass

# =====================================================
# Main
# =====================================================
if __name__ == "__main__":
    keep_alive()
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN missing!")
    load_db_sync()
    
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    if app_bot.job_queue: app_bot.job_queue.run_repeating(auto_backup_job, interval=43200, first=60)
    else: logger.warning("JobQueue is missing! Check your requirements.txt")
        
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("cancel", cancel_command))
    app_bot.add_handler(CommandHandler("unban", unban_command))
    app_bot.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app_bot.add_handler(CallbackQueryHandler(button_handler))
    app_bot.add_error_handler(global_error_handler)

    logger.info("PlayZone Final Perfect Edition Running...")
    app_bot.run_polling(drop_pending_updates=True)
