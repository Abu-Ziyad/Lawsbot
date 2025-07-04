import logging
import requests
import functools
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- الإعدادات والمعلومات الأساسية ---
# هام جداً: استبدل هذه القيم بالقيم الجديدة بعد تغييرها
BOT_TOKEN = "7091291853:AAG_nGI5ZxQVABrkdzwZocf9RIqUcU0tc6g"  # <-- ضع توكن البوت الجديد هنا
DEEPSEEK_API_KEY = "sk-4f2be0c09b3c4f518a231f7f4b2d793e" # <-- ضع مفتاح DeepSeek API الجديد هنا
ADMIN_ID = 7097785684  # ID الخاص بك كمدير

# --- متغيرات حالة البوت ---
MONITORING_ENABLED = True  # حالة المراقبة (يعمل بشكل افتراضي)
BANNED_USERNAMES = set()  # قائمة بأسماء المستخدمين المحظورين (استخدام set للسرعة)
FORBIDDEN_NAMES = ["اسم شخص معين", "اسم آخر ممنوع"] # <-- عدّل هذه القائمة

# إعدادات تسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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

# --- وظائف الأوامر الإدارية ---
@admin_only
async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global MONITORING_ENABLED
    MONITORING_ENABLED = True
    await update.message.reply_text("✅ تم تفعيل مراقبة الرسائل.")
    logger.info(f"Monitoring enabled by admin (ID: {ADMIN_ID})")

@admin_only
async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global MONITORING_ENABLED
    MONITORING_ENABLED = False
    await update.message.reply_text("🛑 تم إيقاف مراقبة الرسائل مؤقتاً.")
    logger.info(f"Monitoring disabled by admin (ID: {ADMIN_ID})")

@admin_only
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        # استخراج اسم المستخدم من الرسالة
        username_to_ban = context.args[0].lstrip('@').lower()
        if username_to_ban:
            BANNED_USERNAMES.add(username_to_ban)
            await update.message.reply_text(f"🚫 تمت إضافة المستخدم @{username_to_ban} إلى قائمة الحظر.")
            logger.info(f"Admin banned user: @{username_to_ban}")
        else:
            raise IndexError
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


# --- وظيفة الذكاء الاصطناعي (DeepSeek) ---
def is_message_inappropriate(text: str) -> bool:
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

# --- وظيفة التحقق من الأسماء الممنوعة ---
def contains_forbidden_name(text: str) -> bool:
    for name in FORBIDDEN_NAMES:
        if name.lower() in text.lower():
            logger.info(f"Forbidden name '{name}' found in message.")
            return True
    return False

# --- معالج الرسائل الرئيسي (تم تحديثه) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 1. تحقق أولاً إذا كانت المراقبة مفعلة
    if not MONITORING_ENABLED:
        return

    message = update.message
    if not message or not message.text or message.chat.type not in ['group', 'supergroup']:
        return

    user = message.from_user
    # تجاهل رسائل المدير
    if user.id == ADMIN_ID:
        return

    text = message.text
    reason_for_deletion = ""

    # 2. التحقق إذا كان المستخدم محظوراً
    if user.username and user.username.lower() in BANNED_USERNAMES:
        reason_for_deletion = f"رسالة من مستخدم محظور (@{user.username.lower()})"
    
    # 3. التحقق من وجود اسم ممنوع
    elif contains_forbidden_name(text):
        reason_for_deletion = "ذكر اسم ممنوع"
    
    # 4. إذا لم يكن هناك سبب، قم بالتحقق باستخدام الذكاء الاصطناعي
    else:
        if is_message_inappropriate(text):
            reason_for_deletion = "محتوى مخالف (بناءً على تحليل الذكاء الاصطناعي)"

    # إذا كان هناك سبب للحذف، قم بحذف الرسالة
    if reason_for_deletion:
        try:
            await message.delete()
            logger.info(f"Message from {user.first_name} (@{user.username}) deleted. Reason: {reason_for_deletion}.")
            # يمكنك إلغاء التعليق عن السطر التالي لإرسال تنبيه لك عند كل حذف
            # await context.bot.send_message(chat_id=ADMIN_ID, text=f"تم حذف رسالة من {user.first_name} بسبب: {reason_for_deletion}\nالنص: {text}")
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")

# --- الوظيفة الرئيسية لتشغيل البوت (تم تحديثها) ---
def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    # إضافة معالجات الأوامر الإدارية
    application.add_handler(CommandHandler("start_monitoring", start_monitoring))
    application.add_handler(CommandHandler("stop_monitoring", stop_monitoring))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))

    # إضافة معالج الرسائل العادية
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot is starting with admin commands...")
    application.run_polling()

if __name__ == '__main__':
    main()
