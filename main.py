#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
=========================================================================================
بوت Telegram لتوزيع الألعاب مع نظام حماية متقدم
=========================================================================================

الوصف:
--------
هذا البوت يوفر نظام آمن لتوزيع روابط تحميل الألعاب عبر Telegram مع ربط التحميل بجهاز واحد فقط.

المكونات الرئيسية:
------------------
1. Flask Server: يدير عمليات التحقق والتحميل
2. Telegram Bot: واجهة التفاعل مع المستخدمين
3. SQLite Database: تخزين بيانات المستخدمين والأجهزة
4. HMAC Security: نظام توقيع رقمي لمنع التلاعب

آلية العمل:
-----------
1. المستخدم يختار لعبة من البوت
2. البوت يولد توقيع HMAC فريد للمستخدم واللعبة
3. السيرفر يتحقق من صحة التوقيع
4. عند أول تحميل، يتم ربط الرابط بمعرف الجهاز (IP أو Cookie)
5. أي محاولة تحميل من جهاز آخر يتم رفضها

نظام الأمان (HMAC):
-------------------
- HMAC = Hash-based Message Authentication Code
- يستخدم مفتاح سري (SECRET_KEY) لتوليد توقيع فريد لكل مستخدم
- الصيغة: HMAC(SECRET_KEY, "user_id:game")
- لا يمكن تزوير التوقيع بدون معرفة المفتاح السري
- يضمن أن الرابط صادر من البوت وليس مزور

ربط التحميل بالجهاز:
--------------------
- عند أول تحميل، يتم حفظ معرف الجهاز (device_id)
- معرف الجهاز = IP Address أو Cookie فريد
- أي محاولة تحميل من جهاز آخر يتم رفضها تلقائياً
- يمنع مشاركة الرابط مع أشخاص آخرين

قاعدة البيانات:
----------------
جدول users:
- user_id: معرف المستخدم في Telegram (مفتاح أساسي)
- game: اسم اللعبة المختارة
- device_id: معرف الجهاز المرتبط (يتم حفظه عند أول تحميل)
- downloads: عدد مرات التحميل (للإحصائيات)

المتطلبات:
----------
pip install flask telegram python-telegram-bot aiohttp nest-asyncio

المطور: تم تحسينه وتوثيقه بواسطة Alex
التاريخ: 2026-01-02
=========================================================================================
"""

# =========================
# IMPORTS - المكتبات المطلوبة
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
import logging
from threading import Thread
from datetime import datetime
from flask import Flask, request, jsonify, redirect, render_template_string, make_response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from typing import Optional, Tuple

# =========================
# LOGGING CONFIGURATION - إعداد نظام السجلات
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# =========================
# CONFIGURATION - الإعدادات الأساسية
# =========================

# توكن البوت من BotFather
TOKEN = "7886094616:AAE15btVEobgTi0Xo4i87X416dquNAfCLQk"

# معرف الأدمن في Telegram (لإرسال الإشعارات)
ADMIN_CHAT_ID = 1077911771

# رابط السيرفر (يجب تغييره عند النشر على PythonAnywhere أو Heroku)
SERVER_HOST = "https://gfdbgta.pythonanywhere.com"

# مفتاح HMAC السري - يجب أن يكون قوي وفريد
# تحذير: لا تشارك هذا المفتاح مع أحد!
SECRET_KEY = b"ta_smg#F9!KX7@R2$wZ%M8^"

# كلمة سر لوحة التحكم
ADMIN_PASSWORD = "ta_smg!Z9@2026#"

# روابط تحميل الألعاب (Dropbox مع ?dl=1 للتحميل المباشر)
DOWNLOAD_LINKS = {
    "thechallenge": "https://www.dropbox.com/scl/fi/3erw8rjjv3gcx01op7iu0/The-Challenge.apk?dl=1",
    "chickenlife": "https://www.dropbox.com/scl/fi/0v4lovtvvlxsuezu3jerh/Chicken-Life.apk?dl=1"
}

# أسماء الألعاب للعرض
GAME_NAMES = {
    "thechallenge": "🎮 The Challenge",
    "chickenlife": "🐔 Chicken Life"
}

# =========================
# DATABASE SETUP - إعداد قاعدة البيانات
# =========================

def init_database() -> sqlite3.Connection:
    """
    تهيئة قاعدة البيانات وإنشاء الجداول المطلوبة
    
    الجداول:
    --------
    users: يحتوي على معلومات المستخدمين والأجهزة المرتبطة
    
    Returns:
    --------
    sqlite3.Connection: اتصال بقاعدة البيانات
    """
    try:
        db = sqlite3.connect("db.sqlite", check_same_thread=False)
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                game TEXT NOT NULL,
                device_id TEXT,
                downloads INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_download TIMESTAMP
            )
        """)
        db.commit()
        logger.info("✅ تم تهيئة قاعدة البيانات بنجاح")
        return db
    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
        raise

# إنشاء اتصال قاعدة البيانات
db = init_database()

# =========================
# SECURITY FUNCTIONS - دوال الأمان
# =========================

def sign(user_id: str, game: str) -> str:
    """
    توليد توقيع HMAC فريد لكل مستخدم ولعبة
    
    آلية العمل:
    -----------
    1. يتم دمج user_id و game في نص واحد
    2. يتم تشفير النص باستخدام HMAC-SHA256 مع المفتاح السري
    3. النتيجة: توقيع رقمي فريد لا يمكن تزويره
    
    Parameters:
    -----------
    user_id: معرف المستخدم في Telegram
    game: اسم اللعبة
    
    Returns:
    --------
    str: التوقيع الرقمي (64 حرف hex)
    
    مثال:
    -----
    >>> sign("123456", "thechallenge")
    'a1b2c3d4e5f6...'
    """
    try:
        message = f"{user_id}:{game}".encode('utf-8')
        signature = hmac.new(SECRET_KEY, message, hashlib.sha256).hexdigest()
        logger.debug(f"🔐 تم توليد توقيع للمستخدم {user_id} - اللعبة {game}")
        return signature
    except Exception as e:
        logger.error(f"❌ خطأ في توليد التوقيع: {e}")
        raise

def verify(user_id: str, game: str, sig: str) -> bool:
    """
    التحقق من صحة التوقيع الرقمي
    
    آلية العمل:
    -----------
    1. يتم توليد توقيع جديد للمستخدم واللعبة
    2. يتم مقارنة التوقيع الجديد مع التوقيع المرسل
    3. استخدام hmac.compare_digest لمنع هجمات timing attacks
    
    Parameters:
    -----------
    user_id: معرف المستخدم
    game: اسم اللعبة
    sig: التوقيع المراد التحقق منه
    
    Returns:
    --------
    bool: True إذا كان التوقيع صحيح، False إذا كان مزور
    """
    try:
        expected_sig = sign(user_id, game)
        is_valid = hmac.compare_digest(expected_sig, sig)
        
        if is_valid:
            logger.info(f"✅ توقيع صحيح للمستخدم {user_id}")
        else:
            logger.warning(f"⚠️ توقيع غير صحيح للمستخدم {user_id}")
        
        return is_valid
    except Exception as e:
        logger.error(f"❌ خطأ في التحقق من التوقيع: {e}")
        return False

# =========================
# DATABASE FUNCTIONS - دوال قاعدة البيانات
# =========================

def get_user(user_id: str) -> Optional[Tuple]:
    """
    جلب معلومات المستخدم من قاعدة البيانات
    
    Parameters:
    -----------
    user_id: معرف المستخدم
    
    Returns:
    --------
    Optional[Tuple]: بيانات المستخدم أو None إذا لم يكن موجود
    """
    try:
        cur = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return cur.fetchone()
    except Exception as e:
        logger.error(f"❌ خطأ في جلب بيانات المستخدم {user_id}: {e}")
        return None

def register_user(user_id: str, game: str) -> bool:
    """
    تسجيل مستخدم جديد في قاعدة البيانات
    
    Parameters:
    -----------
    user_id: معرف المستخدم
    game: اللعبة المختارة
    
    Returns:
    --------
    bool: True إذا تم التسجيل بنجاح
    """
    try:
        db.execute(
            "INSERT OR IGNORE INTO users(user_id, game) VALUES (?, ?)",
            (user_id, game)
        )
        db.commit()
        logger.info(f"✅ تم تسجيل المستخدم {user_id} - اللعبة {game}")
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في تسجيل المستخدم {user_id}: {e}")
        return False

def bind_device(user_id: str, device_id: str) -> bool:
    """
    ربط جهاز المستخدم بالرابط (يحدث عند أول تحميل)
    
    Parameters:
    -----------
    user_id: معرف المستخدم
    device_id: معرف الجهاز (IP أو Cookie)
    
    Returns:
    --------
    bool: True إذا تم الربط بنجاح
    """
    try:
        db.execute(
            """UPDATE users 
               SET device_id=?, downloads=downloads+1, last_download=CURRENT_TIMESTAMP 
               WHERE user_id=?""",
            (device_id, user_id)
        )
        db.commit()
        logger.info(f"✅ تم ربط الجهاز {device_id} للمستخدم {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في ربط الجهاز للمستخدم {user_id}: {e}")
        return False

def increment_download(user_id: str) -> bool:
    """
    زيادة عداد التحميلات للمستخدم
    
    Parameters:
    -----------
    user_id: معرف المستخدم
    
    Returns:
    --------
    bool: True إذا تم التحديث بنجاح
    """
    try:
        db.execute(
            "UPDATE users SET downloads=downloads+1, last_download=CURRENT_TIMESTAMP WHERE user_id=?",
            (user_id,)
        )
        db.commit()
        logger.info(f"✅ تم تحديث عداد التحميل للمستخدم {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في تحديث عداد التحميل: {e}")
        return False

# =========================
# FLASK SERVER - خادم الويب
# =========================

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # لدعم العربية في JSON

@app.route("/")
def home():
    """
    الصفحة الرئيسية - للتحقق من أن السيرفر يعمل
    """
    return jsonify({
        "status": "running",
        "message": "✅ Server is running",
        "timestamp": datetime.now().isoformat()
    })

@app.route("/authorize", methods=["POST"])
def authorize():
    """
    نقطة التحقق من صلاحية المستخدم
    
    آلية العمل:
    -----------
    1. استقبال بيانات المستخدم والتوقيع من البوت
    2. التحقق من صحة التوقيع باستخدام HMAC
    3. تسجيل المستخدم في قاعدة البيانات إذا لم يكن موجود
    4. إرجاع رابط التحميل الفريد
    
    Request Body:
    -------------
    {
        "user_id": "123456",
        "game": "thechallenge",
        "signature": "a1b2c3..."
    }
    
    Response:
    ---------
    Success: {"url": "/download/123456"}
    Error: {"error": "unauthorized"}, 403
    """
    try:
        data = request.get_json()
        
        if not data:
            logger.warning("⚠️ طلب authorize بدون بيانات")
            return jsonify({"error": "no data provided"}), 400
        
        user_id = str(data.get("user_id", ""))
        game = data.get("game", "")
        sig = data.get("signature", "")
        
        # التحقق من وجود جميع البيانات المطلوبة
        if not all([user_id, game, sig]):
            logger.warning(f"⚠️ بيانات ناقصة في طلب authorize")
            return jsonify({"error": "missing parameters"}), 400
        
        # التحقق من أن اللعبة موجودة
        if game not in DOWNLOAD_LINKS:
            logger.warning(f"⚠️ لعبة غير موجودة: {game}")
            return jsonify({"error": "invalid game"}), 400
        
        # التحقق من صحة التوقيع
        if not verify(user_id, game, sig):
            logger.warning(f"⚠️ محاولة وصول غير مصرح بها من {user_id}")
            return jsonify({"error": "unauthorized"}), 403
        
        # تسجيل المستخدم
        register_user(user_id, game)
        
        logger.info(f"✅ تم التحقق من المستخدم {user_id} - اللعبة {game}")
        return jsonify({"url": f"/download/{user_id}"})
    
    except Exception as e:
        logger.error(f"❌ خطأ في authorize: {e}")
        return jsonify({"error": "internal server error"}), 500

@app.route("/download/<user_id>")
def download(user_id: str):
    """
    صفحة التحميل - ربط الرابط بالجهاز وإعادة التوجيه للتحميل
    
    آلية العمل:
    -----------
    1. جلب معرف الجهاز (IP أو Cookie)
    2. التحقق من أن المستخدم مسجل
    3. إذا كان أول تحميل: ربط الجهاز بالمستخدم
    4. إذا كان الجهاز مختلف: رفض التحميل
    5. إعادة التوجيه لرابط التحميل الفعلي
    
    Parameters:
    -----------
    user_id: معرف المستخدم في URL
    
    Returns:
    --------
    - إعادة توجيه لرابط التحميل (إذا كان مصرح)
    - رسالة خطأ (إذا كان غير مصرح)
    """
    try:
        # الحصول على معرف الجهاز (Cookie أو IP)
        device_id = request.cookies.get("device_id") or str(request.remote_addr)
        logger.info(f"📱 طلب تحميل من المستخدم {user_id} - الجهاز {device_id}")
        
        # جلب بيانات المستخدم
        user_data = get_user(user_id)
        
        if not user_data:
            logger.warning(f"⚠️ مستخدم غير موجود: {user_id}")
            return render_template_string("""
                <!DOCTYPE html>
                <html dir="rtl">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>خطأ</title>
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f5f5f5; }
                        .error { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                        h1 { color: #e74c3c; }
                    </style>
                </head>
                <body>
                    <div class="error">
                        <h1>❌ خطأ</h1>
                        <p>لم يتم السماح لك بالتحميل. يرجى التواصل مع البوت أولاً.</p>
                    </div>
                </body>
                </html>
            """), 403
        
        # استخراج بيانات المستخدم
        _, game, saved_device, downloads, _, _ = user_data
        
        # التحقق من ربط الجهاز
        if saved_device and saved_device != device_id:
            logger.warning(f"🚫 محاولة تحميل من جهاز غير مصرح: {device_id} (المصرح: {saved_device})")
            return render_template_string("""
                <!DOCTYPE html>
                <html dir="rtl">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>غير مصرح</title>
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f5f5f5; }
                        .error { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                        h1 { color: #e67e22; }
                    </style>
                </head>
                <body>
                    <div class="error">
                        <h1>🚫 جهاز غير مصرح</h1>
                        <p>هذا الرابط مرتبط بجهاز آخر ولا يمكن استخدامه على هذا الجهاز.</p>
                        <p>كل رابط يعمل على جهاز واحد فقط لحماية حقوق المطور.</p>
                    </div>
                </body>
                </html>
            """), 403
        
        # ربط الجهاز إذا كان أول تحميل
        if not saved_device:
            bind_device(user_id, device_id)
            logger.info(f"✅ تم ربط الجهاز {device_id} للمستخدم {user_id}")
        else:
            increment_download(user_id)
        
        # إعادة التوجيه لرابط التحميل
        download_url = DOWNLOAD_LINKS.get(game)
        if not download_url:
            logger.error(f"❌ رابط تحميل غير موجود للعبة: {game}")
            return "❌ خطأ في رابط التحميل", 500
        
        logger.info(f"✅ تحميل ناجح للمستخدم {user_id} - اللعبة {game}")
        
        # إنشاء استجابة مع Cookie لحفظ معرف الجهاز
        response = make_response(redirect(download_url))
        response.set_cookie('device_id', device_id, max_age=365*24*60*60)  # صالح لمدة سنة
        return response
    
    except Exception as e:
        logger.error(f"❌ خطأ في download: {e}")
        return "❌ حدث خطأ أثناء التحميل", 500

# =========================
# ADMIN PANEL - لوحة التحكم
# =========================

@app.route("/admin")
def admin_panel():
    """
    لوحة تحكم الأدمن - عرض جميع المستخدمين والإحصائيات
    
    الوصول:
    -------
    يتطلب كلمة سر: /admin?pass=كلمة_السر
    
    المعلومات المعروضة:
    -------------------
    - معرف المستخدم
    - اللعبة المختارة
    - معرف الجهاز المرتبط
    - عدد مرات التحميل
    - تاريخ التسجيل
    - آخر تحميل
    """
    try:
        # التحقق من كلمة السر
        if request.args.get("pass") != ADMIN_PASSWORD:
            logger.warning(f"⚠️ محاولة دخول غير مصرح للوحة التحكم من {request.remote_addr}")
            return render_template_string("""
                <!DOCTYPE html>
                <html dir="rtl">
                <head>
                    <meta charset="UTF-8">
                    <title>رفض الدخول</title>
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #2c3e50; color: white; }
                    </style>
                </head>
                <body>
                    <h1>🔒 رفض الدخول</h1>
                    <p>كلمة السر غير صحيحة</p>
                </body>
                </html>
            """), 403
        
        # جلب جميع المستخدمين
        users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        total_users = len(users)
        total_downloads = sum(u[3] for u in users)
        
        logger.info(f"✅ دخول لوحة التحكم من {request.remote_addr}")
        
        # بناء جدول HTML
        html = f"""
        <!DOCTYPE html>
        <html dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>لوحة تحكم الأدمن</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: #333;
                    padding: 20px;
                    margin: 0;
                }}
                .container {{
                    max-width: 1400px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 15px;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                    padding: 30px;
                }}
                h1 {{
                    color: #667eea;
                    text-align: center;
                    margin-bottom: 10px;
                }}
                .stats {{
                    display: flex;
                    justify-content: space-around;
                    margin: 20px 0;
                    flex-wrap: wrap;
                }}
                .stat-box {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 20px;
                    border-radius: 10px;
                    text-align: center;
                    min-width: 200px;
                    margin: 10px;
                }}
                .stat-box h3 {{
                    margin: 0;
                    font-size: 36px;
                }}
                .stat-box p {{
                    margin: 5px 0 0 0;
                    opacity: 0.9;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    background: #fff;
                    margin-top: 20px;
                    border-radius: 10px;
                    overflow: hidden;
                }}
                th, td {{
                    padding: 15px;
                    text-align: center;
                    border-bottom: 1px solid #f0f0f0;
                }}
                th {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    font-weight: bold;
                    text-transform: uppercase;
                    font-size: 12px;
                    letter-spacing: 1px;
                }}
                tr:hover {{
                    background-color: #f8f9ff;
                }}
                .downloads {{
                    color: #27ae60;
                    font-weight: bold;
                    font-size: 18px;
                }}
                .device-bound {{
                    color: #27ae60;
                    font-weight: bold;
                }}
                .device-missing {{
                    color: #e67e22;
                    font-weight: bold;
                }}
                .game-badge {{
                    display: inline-block;
                    padding: 5px 15px;
                    border-radius: 20px;
                    font-size: 12px;
                    font-weight: bold;
                }}
                .game-thechallenge {{
                    background: #3498db;
                    color: white;
                }}
                .game-chickenlife {{
                    background: #f39c12;
                    color: white;
                }}
                .timestamp {{
                    font-size: 11px;
                    color: #7f8c8d;
                }}
                @media (max-width: 768px) {{
                    table {{
                        font-size: 12px;
                    }}
                    th, td {{
                        padding: 8px;
                    }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🛠️ لوحة تحكم الأدمن</h1>
                <p style="text-align: center; color: #7f8c8d;">عرض جميع المستخدمين والإحصائيات</p>
                
                <div class="stats">
                    <div class="stat-box">
                        <h3>{total_users}</h3>
                        <p>👥 إجمالي المستخدمين</p>
                    </div>
                    <div class="stat-box">
                        <h3>{total_downloads}</h3>
                        <p>⬇️ إجمالي التحميلات</p>
                    </div>
                    <div class="stat-box">
                        <h3>{len([u for u in users if u[2]])}</h3>
                        <p>📱 أجهزة مرتبطة</p>
                    </div>
                </div>
                
                <table>
                    <tr>
                        <th>معرف المستخدم</th>
                        <th>اللعبة</th>
                        <th>معرف الجهاز</th>
                        <th>التحميلات</th>
                        <th>تاريخ التسجيل</th>
                        <th>آخر تحميل</th>
                    </tr>
        """
        
        for user in users:
            user_id, game, device, downloads, created_at, last_download = user
            
            # تنسيق معرف الجهاز
            device_display = device if device else "غير مرتبط"
            device_class = "device-bound" if device else "device-missing"
            
            # تنسيق اسم اللعبة
            game_display = GAME_NAMES.get(game, game)
            game_class = f"game-{game}"
            
            # تنسيق التواريخ
            created_display = created_at.split('.')[0] if created_at else "-"
            last_download_display = last_download.split('.')[0] if last_download else "-"
            
            html += f"""
                <tr>
                    <td><strong>{user_id}</strong></td>
                    <td><span class="game-badge {game_class}">{game_display}</span></td>
                    <td class="{device_class}">{device_display}</td>
                    <td class="downloads">{downloads}</td>
                    <td class="timestamp">{created_display}</td>
                    <td class="timestamp">{last_download_display}</td>
                </tr>
            """
        
        html += """
                </table>
            </div>
        </body>
        </html>
        """
        
        return html
    
    except Exception as e:
        logger.error(f"❌ خطأ في لوحة التحكم: {e}")
        return "❌ حدث خطأ في تحميل لوحة التحكم", 500

# =========================
# FLASK THREAD - تشغيل Flask في خيط منفصل
# =========================

def run_flask():
    """
    تشغيل Flask server في خيط منفصل
    
    ملاحظة:
    -------
    - يعمل على المنفذ 8080
    - debug=False للإنتاج
    - use_reloader=False لتجنب تشغيل الكود مرتين
    """
    try:
        logger.info("🌐 بدء تشغيل Flask server...")
        app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"❌ خطأ في تشغيل Flask: {e}")

def keep_alive():
    """
    إبقاء السيرفر يعمل في الخلفية
    
    آلية العمل:
    -----------
    - يتم إنشاء Thread منفصل لتشغيل Flask
    - daemon=True يعني أن الخيط سيتوقف عند إيقاف البرنامج الرئيسي
    """
    try:
        t = Thread(target=run_flask, daemon=True)
        t.start()
        logger.info("✅ تم بدء Flask server في الخلفية")
    except Exception as e:
        logger.error(f"❌ خطأ في keep_alive: {e}")

# =========================
# TELEGRAM BOT - بوت تيليجرام
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالج أمر /start - رسالة الترحيب وعرض الألعاب
    
    آلية العمل:
    -----------
    1. عرض رسالة ترحيب للمستخدم
    2. شرح مبسط لآلية العمل (بدون تفاصيل تقنية)
    3. عرض أزرار اختيار الألعاب
    
    ملاحظة:
    -------
    - الرسائل بسيطة وواضحة للمستخدم النهائي
    - لا تحتوي على مصطلحات تقنية
    """
    try:
        user = update.effective_user
        logger.info(f"👤 مستخدم جديد: {user.id} - {user.first_name}")
        
        # إنشاء أزرار الألعاب
        keyboard = [
            [InlineKeyboardButton(GAME_NAMES["thechallenge"], callback_data="thechallenge")],
            [InlineKeyboardButton(GAME_NAMES["chickenlife"], callback_data="chickenlife")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # رسالة الترحيب (بسيطة وواضحة للمستخدم)
        welcome_message = (
            f"👋 أهلاً بك *{user.first_name}* في بوت تحميل الألعاب!\n\n"
            "📱 *معلومات مهمة:*\n"
            "• الألعاب متوفرة حالياً لأجهزة *الأندرويد* فقط\n"
            "• رابط التحميل يعمل على *جهازك فقط*\n"
            "• لا يمكن مشاركة الرابط مع أشخاص آخرين\n\n"
            "🎮 *اختر اللعبة التي تريد تحميلها:*"
        )
        
        await update.message.reply_text(
            welcome_message,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        
        logger.info(f"✅ تم إرسال رسالة الترحيب للمستخدم {user.id}")
    
    except Exception as e:
        logger.error(f"❌ خطأ في معالج start: {e}")
        await update.message.reply_text(
            "❌ عذراً، حدث خطأ. يرجى المحاولة مرة أخرى لاحقاً."
        )

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالج اختيار اللعبة - توليد رابط التحميل
    
    آلية العمل:
    -----------
    1. المستخدم يضغط على زر اللعبة
    2. البوت يولد توقيع HMAC
    3. يرسل طلب للسيرفر للتحقق
    4. يرسل رابط التحميل للمستخدم
    
    ملاحظة:
    -------
    - جميع العمليات الأمنية تتم في الخلفية
    - المستخدم يرى فقط رسائل بسيطة
    """
    try:
        query = update.callback_query
        await query.answer()
        
        game = query.data
        user = query.from_user
        user_id = str(user.id)
        
        logger.info(f"🎮 المستخدم {user_id} اختار اللعبة {game}")
        
        # تسجيل المستخدم في قاعدة البيانات
        register_user(user_id, game)
        
        # توليد التوقيع الأمني
        signature = sign(user_id, game)
        
        # إعداد البيانات للإرسال للسيرفر
        payload = {
            "user_id": user_id,
            "game": game,
            "signature": signature
        }
        
        # إرسال طلب للسيرفر
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{SERVER_HOST}/authorize",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        download_url = f"{SERVER_HOST}{data['url']}"
                        
                        # رسالة التحميل (بسيطة وواضحة)
                        game_name = GAME_NAMES.get(game, game)
                        message = (
                            f"✅ *تم إنشاء رابط التحميل بنجاح!*\n\n"
                            f"🎮 اللعبة: *{game_name}*\n\n"
                            f"⬇️ *رابط التحميل:*\n{download_url}\n\n"
                            f"⚠️ *تنبيهات مهمة:*\n"
                            f"• الرابط يعمل على جهازك فقط\n"
                            f"• لا تشارك الرابط مع أحد\n"
                            f"• إذا لم يعمل الرابط، تواصل معنا\n\n"
                            f"🎉 استمتع باللعبة!"
                        )
                        
                        await query.message.reply_text(message, parse_mode="Markdown")
                        logger.info(f"✅ تم إرسال رابط التحميل للمستخدم {user_id}")
                    else:
                        raise Exception(f"Server returned status {response.status}")
        
        except asyncio.TimeoutError:
            logger.error(f"⏱️ انتهت مهلة الاتصال بالسيرفر للمستخدم {user_id}")
            await query.message.reply_text(
                "⏱️ عذراً، انتهت مهلة الاتصال بالسيرفر.\n"
                "يرجى المحاولة مرة أخرى بعد قليل."
            )
        
        except Exception as e:
            logger.error(f"❌ خطأ في الاتصال بالسيرفر: {e}")
            await query.message.reply_text(
                "❌ عذراً، حدث خطأ في الاتصال بالسيرفر.\n"
                "يرجى المحاولة مرة أخرى أو التواصل مع الدعم."
            )
    
    except Exception as e:
        logger.error(f"❌ خطأ في معالج choose_game: {e}")
        try:
            await query.message.reply_text(
                "❌ عذراً، حدث خطأ. يرجى المحاولة مرة أخرى."
            )
        except:
            pass

# =========================
# MAIN - البرنامج الرئيسي
# =========================

def main():
    """
    الدالة الرئيسية - تشغيل البوت والسيرفر
    
    آلية العمل:
    -----------
    1. تشغيل Flask server في خيط منفصل
    2. إعداد nest_asyncio للسماح بتشغيل asyncio في Jupyter/PythonAnywhere
    3. بناء تطبيق Telegram bot
    4. إضافة معالجات الأوامر
    5. بدء تشغيل البوت
    
    معالجة الإيقاف:
    ---------------
    - SIGINT: Ctrl+C
    - SIGTERM: إيقاف من النظام
    """
    try:
        # معالجة إشارات الإيقاف
        import signal
        def signal_handler(sig, frame):
            logger.info("🛑 تم استقبال إشارة إيقاف...")
            db.close()
            logger.info("✅ تم إغلاق قاعدة البيانات")
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # بدء Flask server
        keep_alive()
        
        # إعداد nest_asyncio (مطلوب لـ PythonAnywhere)
        nest_asyncio.apply()
        
        # بناء تطبيق البوت
        logger.info("🤖 بدء تشغيل Telegram Bot...")
        app_bot = ApplicationBuilder().token(TOKEN).build()
        
        # إضافة معالجات الأوامر
        app_bot.add_handler(CommandHandler("start", start))
        app_bot.add_handler(CallbackQueryHandler(choose_game))
        
        logger.info("✅ تم تهيئة البوت بنجاح")
        logger.info("🚀 البوت يعمل الآن ويستقبل الرسائل...")
        logger.info(f"🔗 لوحة التحكم: {SERVER_HOST}/admin?pass={ADMIN_PASSWORD}")
        
        # بدء استقبال الرسائل
        asyncio.run(app_bot.run_polling(drop_pending_updates=True))
    
    except KeyboardInterrupt:
        logger.info("⌨️ تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        logger.error(f"❌ خطأ فادح في البرنامج الرئيسي: {e}")
        raise
    finally:
        db.close()
        logger.info("👋 تم إيقاف البوت بنجاح")

# =========================
# ENTRY POINT - نقطة الدخول
# =========================

if __name__ == "__main__":
    """
    نقطة بدء البرنامج
    
    للتشغيل:
    --------
    python improved_bot.py
    
    للنشر على PythonAnywhere:
    -------------------------
    1. رفع الملف على PythonAnywhere
    2. تعديل SERVER_HOST ليطابق رابط موقعك
    3. تشغيل الملف من Console
    4. إعداد Always-on task (للحسابات المدفوعة)
    
    للنشر على Heroku:
    ------------------
    1. إنشاء Procfile: web: python improved_bot.py
    2. إنشاء requirements.txt بجميع المكتبات
    3. رفع الكود على Heroku
    """
    main()
