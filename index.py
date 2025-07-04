import logging
import requests
import os
import functools
import asyncio
from flask import Flask, request, Response
from telegram import Update, Bot

# --- الإعدادات والمعلومات الأساسية (مضمنة في الكود مباشرة) ---
# هام جداً: قم بتغيير هذه القيم في أقرب وقت ممكن!
BOT_TOKEN = "7091291853:AAHZZ84aMvFmqjv4rVzQh8el2tVJoG9HExA"
DEEPSEEK_API_KEY = "sk-4f2be0c09b3c4f518a231f7f4b2d793e"
ADMIN_ID = 7097785684
# !! هام جداً: غيّر هذا الرابط إلى الرابط الفعلي لخدمتك على Render !!
RENDER_EXTERNAL_URL = "https://api.render.com/deploy/srv-d1k3s8be5dus73e2vt1g?key=oL6deg5FQm4" 

# --- التحقق من أن رابط Render تم وضعه ---
if "your-app-name" in RENDER_EXTERNAL_URL:
    raise ValueError("Please replace 'your-app-name.onrender.com' with your actual Render service URL in the code.")

# --- متغيرات حالة البوت ---
MONITORING_ENABLED = True
BANNED_USERNAMES = set()
FORBIDDEN_NAMES = ["فلان", "اسم شركة منافسة", "كلمة ممنوعة"]

# إعدادات تسجيل الأخطاء (Logs)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- إعداد تطبيق Flask وخادم البوت ---
app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

# --- مُزخرف (Decorator) للتحقق من أن المستخدم هو المدير ---
def admin_only(func):
    @functools.wraps(func)
    async def wrapped(update: Update, context, *args, **kwargs): # context is now just a placeholder
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("هذا الأمر مخصص للمدير فقط.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- وظائف الأوامر الإدارية (لا تحتاج لتعديل) ---
@admin_only
async def start_monitoring(update: Update, context):
    global MONITORING_ENABLED
    MONITORING_ENABLED = True
    await update.message.reply_text("✅ تم تفعيل مراقبة الرسائل.")

@admin_only
async def stop_monitoring(update: Update, context):
    global MONITORING_ENABLED
    MONITORING_ENABLED = False
    await update.message.reply_text("🛑 تم إيقاف مراقبة الرسائل مؤقتاً.")

@admin_only
async def ban_user(update: Update, context):
    try:
        args = update.message.text.split()[1:]
        username_to_ban = args[0].lstrip('@').lower()
        BANNED_USERNAMES.add(username_to_ban)
        await update.message.reply_text(f"🚫 تمت إضافة @{username_to_ban} إلى قائمة الحظر.")
    except IndexError:
        await update.message.reply_text("الاستخدام: /ban @username")

@admin_only
async def unban_user(update: Update, context):
    try:
        args = update.message.text.split()[1:]
        username_to_unban = args[0].lstrip('@').lower()
        if username_to_unban in BANNED_USERNAMES:
            BANNED_USERNAMES.remove(username_to_unban)
            await update.message.reply_text(f"👍 تم إزالة @{username_to_unban} من قائمة الحظر.")
        else:
            await update.message.reply_text(f"المستخدم @{username_to_unban} ليس في قائمة الحظر.")
    except IndexError:
        await update.message.reply_text("الاستخدام: /unban @username")

# --- وظائف التحقق (لا تحتاج لتعديل) ---
def is_message_inappropriate(text: str) -> bool:
    api_url = "https://api.deepseek.com/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    system_prompt = "أجب بـ 'نعم' فقط إذا كانت الرسالة التالية تحتوي على إساءة، كلام بذيء، محتوى غير قانوني، عنصرية، أو تهديد. أجب بـ 'لا' إذا كانت عادية. لا تقدم أي شرح."
    payload = {"model": "deepseek-chat", "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}], "temperature": 0.1, "max_tokens": 5}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        ai_response = response.json()['choices'][0]['message']['content'].strip().lower()
        return "نعم" in ai_response
    except Exception as e:
        logger.error(f"Error with DeepSeek API: {e}")
        return False

def contains_forbidden_name(text: str) -> bool:
    return any(name.lower() in text.lower() for name in FORBIDDEN_NAMES)

# --- معالج الرسائل الرئيسي ---
async def process_message(update: Update, context):
    if not MONITORING_ENABLED or not update.message or not update.message.text: return
    user = update.message.from_user
    if user.id == ADMIN_ID: return
    text = update.message.text
    reason_for_deletion = ""
    if user.username and user.username.lower() in BANNED_USERNAMES:
        reason_for_deletion = f"رسالة من مستخدم محظور (@{user.username.lower()})"
    elif contains_forbidden_name(text):
        reason_for_deletion = "ذكر اسم ممنوع"
    elif is_message_inappropriate(text):
        reason_for_deletion = "محتوى مخالف (AI)"
    if reason_for_deletion:
        try:
            await update.message.delete()
            logger.info(f"Message from {user.first_name} deleted. Reason: {reason_for_deletion}.")
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")

# --- قاموس لتوجيه الأوامر ---
COMMAND_HANDLERS = {
    "/start_monitoring": start_monitoring,
    "/stop_monitoring": stop_monitoring,
    "/ban": ban_user,
    "/unban": unban_user,
}

# --- نقطة دخول الـ Webhook ---
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook_handler():
    update_data = request.get_json(force=True)
    update = Update.de_json(data=update_data, bot=bot)
    
    async def process_update():
        if update.message and update.message.text:
            command = update.message.text.split()[0]
            handler = COMMAND_HANDLERS.get(command)
            if handler:
                await handler(update, None)  # <-- التغيير هنا
            else:
                await process_message(update, None) # <-- والتغيير هنا

    asyncio.run(process_update())
    return Response('ok', status=200)

# --- إعداد الـ Webhook عند بدء التشغيل ---
def setup_webhook():
    webhook_endpoint = f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_endpoint}&drop_pending_updates=true"
    response = requests.get(url)
    if response.status_code == 200 and response.json().get("ok"):
        logger.info(f"Webhook set successfully to {webhook_endpoint}")
    else:
        logger.error(f"Failed to set webhook: {response.text}")
        raise RuntimeError("Webhook setup failed!")

# --- نقطة انطلاق الخادم ---
if __name__ == "__main__":
    setup_webhook()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
