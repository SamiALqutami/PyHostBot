import os
import json
import asyncio
import logging
import traceback
from flask import Flask, request, jsonify, render_template_string
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes
from upstash_redis import Redis

# إعدادات اللوج
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.ERROR)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# الاتصال بقاعدة البيانات باستخدام الرابط الخاص بك
# ملاحظة: يفضل دائماً وضع الروابط الحساسة في "Environment Variables" على Vercel
REDIS_URL = "rediss://default:AUgwAAIncDExZDk4NjZmM2YyY2Q0YzI0YjFmZjk0NjBkNDg3NDA3MnAxMTg0ODA@neutral-muskox-18480.upstash.io:6379"
redis = Redis.from_url(REDIS_URL)

# الرابط الأساسي (سيتم جلبه تلقائياً من إعدادات فيرسل)
VERCEL_URL = os.environ.get('VERCEL_URL', '')

# --- واجهة المستخدم (تطبيق مصغر) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <title>Serverless Bot Host</title>
    <style>
        body { font-family: sans-serif; background-color: var(--tg-theme-bg-color, #fff); color: var(--tg-theme-text-color, #000); padding: 20px; box-sizing: border-box; }
        h3 { text-align: center; color: var(--tg-theme-button-color, #3390ec); }
        .card { background: var(--tg-theme-secondary-bg-color, #f5f5f5); padding: 15px; border-radius: 12px; margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; font-size: 14px; }
        input, textarea { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid #ddd; margin-bottom: 10px; box-sizing: border-box; background: #fff; color: #000; }
        textarea { height: 180px; font-family: monospace; font-size: 13px; }
        button { width: 100%; padding: 14px; background-color: var(--tg-theme-button-color, #3390ec); color: #fff; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; }
        #status { text-align: center; margin-top: 10px; font-weight: bold; }
    </style>
</head>
<body>
    <h3>☁️ استضافة بوتات سحابية</h3>
    <div class="card">
        <label>توكن البوت (Token):</label>
        <input type="text" id="token" placeholder="لصق التوكن هنا...">
    </div>
    
    <div class="card">
        <label>كود البوت (Python):</label>
        <textarea id="code">
async def handle_message(update, context):
    # مثال: رد على الرسالة
    if update.message and update.message.text:
        txt = update.message.text
        await update.message.reply_text(f"تمت الاستضافة بنجاح! قلت: {txt}")
        </textarea>
    </div>

    <button onclick="deploy()">رفع وتشغيل البوت 🚀</button>
    <div id="status"></div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        async function deploy() {
            const token = document.getElementById('token').value.trim();
            const code = document.getElementById('code').value;
            const status = document.getElementById('status');

            if (!token || !code) return tg.showAlert("الرجاء ملء جميع الحقول");

            status.innerHTML = "⏳ جاري الرفع...";
            status.style.color = "orange";

            try {
                const res = await fetch('/api/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ token, code })
                });
                const data = await res.json();
                
                if (data.ok) {
                    status.innerHTML = `✅ تم! البوت يعمل الآن.<br>@${data.username}`;
                    status.style.color = "green";
                    tg.HapticFeedback.notificationOccurred('success');
                } else {
                    status.innerHTML = "❌ " + data.error;
                    status.style.color = "red";
                    tg.HapticFeedback.notificationOccurred('error');
                }
            } catch (e) {
                status.innerHTML = "❌ خطأ في الاتصال";
                status.style.color = "red";
            }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

# --- تسجيل البوت ---
@app.route('/api/register', methods=['POST'])
async def register():
    data = request.json
    token = data.get('token')
    code = data.get('code')

    if not token or not code:
        return jsonify({"ok": False, "error": "بيانات ناقصة"})

    try:
        bot = Bot(token=token)
        bot_info = await bot.get_me()
        
        # حفظ الكود والبيانات في سجل واحد في Redis
        bot_data = json.dumps({"code": code, "username": bot_info.username})
        redis.set(f"bot:{token}", bot_data)

        # ضبط الويب هوك
        # نستخدم طلب البروتوكول https لأن فيرسل يوفر ذلك
        full_url = f"https://{VERCEL_URL}" if VERCEL_URL else request.host_url.rstrip('/')
        webhook_url = f"{full_url}/api/webhook/{token}"
        
        await bot.set_webhook(webhook_url)

        return jsonify({"ok": True, "username": bot_info.username})
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return jsonify({"ok": False, "error": "توكن غير صالح أو خطأ تقني"})

# --- المحرك (The Engine) ---
@app.route('/api/webhook/<token>', methods=['POST'])
async def webhook(token):
    stored_data = redis.get(f"bot:{token}")
    
    if not stored_data:
        return "Not found", 404

    try:
        bot_config = json.loads(stored_data)
        user_code = bot_config.get("code")
        
        if not user_code: return "No code", 404

        update_data = request.json
        
        app_ptb = Application.builder().token(token).build()
        await app_ptb.initialize()
        
        update = Update.de_json(update_data, app_ptb.bot)
        
        # بيئة التنفيذ
        exec_scope = {
            "Update": Update,
            "ContextTypes": ContextTypes,
            "json": json,
            "asyncio": asyncio,
            "logger": logger
        }
        
        exec(user_code, exec_scope)
        
        if 'handle_message' in exec_scope:
            handler = exec_scope['handle_message']
            # تمرير الـ context المناسب لـ PTB v20+
            context = app_ptb.default_context_types.context.from_update(update, app_ptb)
            await handler(update, context)
        
        await app_ptb.shutdown()
        return "OK", 200

    except Exception as e:
        print(f"Error in bot {token}: {e}")
        traceback.print_exc()
        return "Error", 500

if __name__ == '__main__':
    app.run(debug=True)
