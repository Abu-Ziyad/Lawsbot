import logging
import requests
import os
import functools
import asyncio
from flask import Flask, request, Response
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes

# --- الإعدادات والمعلومات الأساسية ---
# سيتم قراءة هذه القيم من متغيرات البيئة (Environment Variables) في Render
BOT_TOKEN = os.environ.get("7091291853:AAHZZ84aMvFmqjv4rVzQh8el2tVJoG9HExA")
DEEPSEEK_API_KEY = os.environ.get("sk-4f2be0c09b3c4f518a231f7f4b2d793e")
ADMIN_ID = 7097785684
int(os.environ.get("ADMIN_ID"))
# الرابط الذي سيعمل عليه البوت على Render، سيتم تعيينه تلقائياً
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")

# --- متغيرات حالة البوت (يمكن تخزينها لاحقًا في قاعدة بيانات للثبات) ---
MONITORING_ENABLED = True
BANNED_USERNAMES = set()
FORBIDDEN_NAMES = ["اسم شخص معين", "اسم آخر ممنوع"] # يمكنك تحميلها من ملف أو متغير بيئة أيضاً

# إعدادات تسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- إعداد تطبيق Flask وخادم البوت ---
app = Flask(__name__)
# إنشاء كائن البوت بشكل منفصل
bot = Bot(token=BOT_TOKEN)

# --- مُزخرف (Decorator) للتحقق من أن المستخدم هو المدير ---
def admin_only(func):
    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("هذا الأمر مخصص للمدير فقط.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- وظائف الأوامر الإدارية (تبقى كما هي) ---
@admin_only
async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global MONITORING_ENABLED
    MONITORING_ENABLED = True
    await update.message.reply_text("✅ تم تفعيل مراقبة الرسائل.")
    logger.info("Monitoring enabled by admin.")

@admin_only
async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global MONITORING_ENABLED
    MONITORING_ENABLED = False
    await update.message.reply_text("🛑 تم إيقاف مراقبة الرسائل مؤقتاً.")
    logger.info("Monitoring disabled by admin.")

@admin_only
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        username_to_ban = context.args[0].lstrip('@').lower()
        if username_to_ban:
            BANNED_USERNAMES.add(username_to_ban)
            await update.message.reply_text(f"🚫 تمت إضافة المستخدم @{username_to_ban} إلى قائمة الحظر.")
            logger.info(f"Admin banned user: @{username_to_ban}")
    except (IndexError, ValueError):
        await update.message.reply_text("الاستخدام: /ban @username")

@admin_only
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        username_to_unban = context.args[0].lstrip('@').lower()
        if username_to_unban in BANNED_USERNAMES:
            BANNED_USERNAMES.remove(username_to_unban)
            await update.message.reply_text(f"👍 تم إزالة المستخدم @{username_to_unban} من قائمة الحظر.")
            logger.info(f"Admin unbanned user: @{username_to_unban}")
        else:
            await update.message.reply_text(f"المستخدم @{username_to_unban} ليس في قائمة الحظر.")
    except (IndexError, ValueError):
        await update.message.reply_text("الاستخدام: /unban @username")

# --- وظائف التحقق (تبقى كما هي) ---
def is_message_inappropriate(text: str) -> bool:
    # ... (نفس كود وظيفة DeepSeek من الإصدار السابق)
    api_url = "https://api.deepseek.com/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    system_prompt = ("أنت مشرف محتوى صارم. مهمتك هي تحديد ما إذا كانت الرسالة التالية تحتوي على إساءة، كلام بذيء، محتوى غير قانوني، عنصرية, أو تهديد. أجب بـ 'نعم' فقط إذا كانت مخالفة، و بـ 'لا' إذا كانت عادية. لا تقدم أي شرح.")
    payload = {"model": "deepseek-chat", "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}], "temperature": 0.1, "max_tokens": 10}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        ai_response = response.json()['choices'][0]['message']['content'].strip().lower()
        logger.info(f"AI check for text '{text[:30]}...': AI response is '{ai_response}'")
        return "نعم" in ai_response
    except Exception as e:
        logger.error(f"Error with DeepSeek API: {e}")
        return False

def contains_forbidden_name(text: str) -> bool:
    for name in FORBIDDEN_NAMES:
        if name.lower() in text.lower():
            return True
    return False

# --- معالج الرسائل الرئيسي (مُعدّل قليلاً) ---
async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not MONITORING_ENABLED or not update.message or not update.message.text:
        return

    user = update.message.from_user
    if user.id == ADMIN_ID:
        return

    text = update.message.text
    reason_for_deletion = ""
    
    if user.username and user.username.lower() in BANNED_USERNAMES:
        reason_for_deletion = f"رسالة من مستخدم محظور (@{user.username.lower()})"
    elif contains_forbidden_name(text):
        reason_for_deletion = "ذكر اسم ممنوع"
    elif is_message_inappropriate(text):
        reason_for_deletion = "محتوى مخالف (بناءً على تحليل الذكاء الاصطناعي)"

    if reason_for_deletion:
        try:
            await update.message.delete()
            logger.info(f"Message from {user.first_name} deleted. Reason: {reason_for_deletion}.")
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")

# --- نقطة الدخول الرئيسية للـ Webhook ---
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook_handler():
    # تحويل الطلب القادم من تلجرام إلى كائن Update
    update_data = request.get_json(force=True)
    update = Update.de_json(data=update_data, bot=bot)
    
    # استخدام asyncio لمعالجة الطلب في الخلفية
    # هذا يسمح بالاستجابة لتلجرام بسرعة بينما تتم المعالجة
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # هنا نقوم بتحديد نوع التحديث ومعالجته
    # هذا جزء مبسط، يمكن استخدام مكتبة python-telegram-bot بالكامل
    # ولكن للتبسيط، سنقوم بالتحقق يدوياً
    if update.message and update.message.text:
        text = update.message.text
        if text.startswith('/'): # إذا كانت الرسالة أمرًا
            # هذه طريقة يدوية بسيطة لتوجيه الأوامر
            if text.startswith('/start_monitoring'):
                loop.run_until_complete(start_monitoring(update, None))
            elif text.startswith('/stop_monitoring'):
                loop.run_until_complete(stop_monitoring(update, None))
            elif text.startswith('/ban'):
                # نحتاج إلى استخراج الوسائط (args) يدويًا
                context_args = text.split()[1:] if len(text.split()) > 1 else []
                context_mock = type('Context', (), {'args': context_args})()
                loop.run_until_complete(ban_user(update, context_mock))
            elif text.startswith('/unban'):
                context_args = text.split()[1:] if len(text.split()) > 1 else []
                context_mock = type('Context', (), {'args': context_args})()
                loop.run_until_complete(unban_user(update, context_mock))
        else: # إذا كانت رسالة عادية
            loop.run_until_complete(process_message(update, None))
            
    return Response('ok', status=200)

# --- وظيفة لإعداد الـ Webhook عند بدء التشغيل ---
def setup_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/{BOT_TOKEN}"
    response = requests.get(url)
    if response.status_code == 200 and response.json().get("ok"):
        logger.info(f"Webhook set successfully to {WEBHOOK_URL}")
    else:
        logger.error(f"Failed to set webhook: {response.text}")

if __name__ == "__main__":
    if not all([BOT_TOKEN, DEEPSEEK_API_KEY, ADMIN_ID, WEBHOOK_URL]):
        logger.error("Missing one or more environment variables. Please check your Render config.")
    else:
        # إعداد الـ Webhook مرة واحدة عند بدء تشغيل الخادم
        setup_webhook()
        # تشغيل خادم Flask
        # Render يتوقع أن يعمل الخادم على منفذ 10000
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
