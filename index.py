import logging
import requests
import os
import functools
import asyncio
from flask import Flask, request, Response
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

# --- الإعدادات والمعلومات الأساسية (مضمنة في الكود مباشرة) ---
# تذكر تغيير هذه القيم لاحقاً لحماية حساباتك
BOT_TOKEN = "7091291853:AAHZZ84aMvFmqjv4rVzQh8el2tVJoG9HExA"
DEEPSEEK_API_KEY = "sk-4f2be0c09b3c4f518a231f7f4b2d793e"
ADMIN_ID = 7097785684
RENDER_EXTERNAL_URL = "https://lawsbot.onrender.com"

# --- متغيرات حالة البوت ---
MONITORING_ENABLED = True
BANNED_USERS = set()
FORBIDDEN_NAMES = ["اسم شخص معين", "اسم آخر ممنوع"]

# --- القوانين لتوجيه الذكاء الاصطناعي ---
GROUP_RULES_PROMPT = """
أنت مشرف صارم جداً في مجموعة نقاش اسمها "مناقشات بيت فجار". مهمتك هي تحليل الرسالة التالية وتحديد ما إذا كانت تنتهك أياً من القوانين العشرة التالية.
القوانين هي:
١- عدم الاحترام.
٢- استخدام أي ألفاظ نابية أو شتم أو إهانة.
٣- نشر روابط أو إعلانات.
٤- ذكر أسماء أشخاص حقيقيين أو بياناتهم الشخصية.
٥- نشر صور أو مقاطع مخلة أو مسيئة.
٦- نقاشات خارجة عن الأدب العام أو تثير الفتن.
٧- التلميح أو التهديد أو التحريض أو التنمر.
٨- أي محاولة للتحايل على القوانين.
٩- التحدث في السياسة أو الدين بشكل يثير الجدل.
١٠- أي محتوى غير لائق بشكل عام.

حلل الرسالة بدقة. إذا كانت الرسالة تنتهك **أي قانون** من هذه القوانين، أجب بـ 'نعم' فقط. إذا كانت الرسالة سليمة تماماً، أجب بـ 'لا' فقط. لا تقدم أي شرح أو تفاصيل إضافية.
"""

# إعدادات تسجيل الأخطاء (Logs)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- إعداد تطبيق Flask وخادم البوت ---
app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)
# تم حذف السطر المسبب للمشكلة من هنا

# --- مُزخرف (Decorator) للتحقق من أن المستخدم هو المدير ---
def admin_only(func):
    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id != ADMIN_ID:
            if update.callback_query:
                await update.callback_query.answer("هذا الأمر مخصص للمدير فقط.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- وظائف لوحة التحكم والأزرار ---
@admin_only
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    monitoring_status_text = "إيقاف المراقبة 🛑" if MONITORING_ENABLED else "تشغيل المراقبة ✅"
    monitoring_callback = "toggle_monitoring_off" if MONITORING_ENABLED else "toggle_monitoring_on"
    
    keyboard = [
        [InlineKeyboardButton(monitoring_status_text, callback_data=monitoring_callback)],
        [InlineKeyboardButton(f"عرض المحظورين ({len(BANNED_USERS)})", callback_data="view_banned")],
        [InlineKeyboardButton("إغلاق اللوحة", callback_data="close_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    panel_text = (
        "⚙️ **لوحة تحكم المشرف** ⚙️\n\n"
        "الحالة الحالية للمراقبة: " + ("**مُفعّلة**" if MONITORING_ENABLED else "**مُتوقفة**") + "\n\n"
        "**الأوامر:**\n"
        "`/ban` - (بالرد على رسالة) لحظر المستخدم.\n"
        "`/unban` - (بالرد على رسالة) لرفع الحظر."
    )
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=panel_text, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception as e:
            logger.info(f"Could not edit message, probably unchanged: {e}")
    else:
        await update.message.reply_text(panel_text, reply_markup=reply_markup, parse_mode='Markdown')

@admin_only
async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    global MONITORING_ENABLED
    
    if query.data == "toggle_monitoring_on":
        MONITORING_ENABLED = True
    elif query.data == "toggle_monitoring_off":
        MONITORING_ENABLED = False
    elif query.data == "view_banned":
        if not BANNED_USERS:
            await query.answer("قائمة الحظر فارغة حالياً.", show_alert=True)
        else:
            banned_list_text = "قائمة المستخدمين المحظورين (حسب الـ ID):\n" + "\n".join(f"`{user_id}`" for user_id in BANNED_USERS)
            await query.message.reply_text(banned_list_text, parse_mode='Markdown')
        return
    elif query.data == "close_panel":
        await query.edit_message_text("تم إغلاق لوحة التحكم.")
        return
        
    await show_admin_panel(update, context)

# --- وظائف الحظر والرفع (بطريقة الرد) ---
@admin_only
async def ban_user_by_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ للاستخدام الصحيح، قم بالرد على رسالة الشخص الذي تريد حظره بهذا الأمر.")
        return
    
    user_to_ban = update.message.reply_to_message.from_user
    BANNED_USERS.add(user_to_ban.id)
    await update.message.reply_text(f"🚫 تم حظر المستخدم {user_to_ban.first_name} (`{user_to_ban.id}`) من إرسال الرسائل.")
    logger.info(f"User {user_to_ban.id} BANNED by admin.")

@admin_only
async def unban_user_by_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ للاستخدام الصحيح، قم بالرد على رسالة الشخص الذي تريد رفع الحظر عنه بهذا الأمر.")
        return
        
    user_to_unban = update.message.reply_to_message.from_user
    if user_to_unban.id in BANNED_USERS:
        BANNED_USERS.remove(user_to_unban.id)
        await update.message.reply_text(f"👍 تم رفع الحظر عن المستخدم {user_to_unban.first_name} (`{user_to_unban.id}`).")
    else:
        await update.message.reply_text(f"المستخدم {user_to_unban.first_name} ليس في قائمة الحظر أصلاً.")

# --- وظائف التحقق (DeepSeek والأسماء الممنوعة) ---
def is_message_inappropriate(text: str) -> bool:
    api_url = "https://api.deepseek.com/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-chat", "messages": [{"role": "system", "content": GROUP_RULES_PROMPT}, {"role": "user", "content": text}], "temperature": 0, "max_tokens": 5}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        ai_response = response.json()['choices'][0]['message']['content'].strip().lower()
        logger.info(f"AI check for text '{text[:30]}...': AI response is '{ai_response}'")
        return "نعم" in ai_response
    except Exception as e:
        logger.error(f"Error with DeepSeek API: {e}")
        return False

# --- معالج الرسائل الرئيسي ---
async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not MONITORING_ENABLED or not update.message or not update.message.text or update.message.chat.type not in ['group', 'supergroup']:
        return

    user = update.message.from_user
    if user.id == ADMIN_ID:
        return

    text = update.message.text
    reason_for_deletion = ""
    
    if user.id in BANNED_USERS:
        reason_for_deletion = "رسالة من مستخدم محظور"
    elif any(name.lower() in text.lower() for name in FORBIDDEN_NAMES):
        reason_for_deletion = "ذكر اسم ممنوع"
    elif is_message_inappropriate(text):
        reason_for_deletion = "محتوى مخالف (بناءً على تحليل AI)"
    
    if reason_for_deletion:
        try:
            await update.message.delete()
            logger.info(f"Message from {user.first_name} ({user.id}) deleted. Reason: {reason_for_deletion}.")
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")

# --- قاموس لتوجيه الأوامر ---
COMMAND_HANDLERS = {
    "/panel": show_admin_panel,
    "/ban": ban_user_by_reply,
    "/unban": unban_user_by_reply,
}

# --- نقطة دخول الـ Webhook ---
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook_handler():
    update_data = request.get_json(force=True)
    update = Update.de_json(data=update_data, bot=bot)
    
    async def process_update():
        if update.callback_query:
            # تمرير None مكان الـ context الذي لم نعد نستخدمه
            await button_callback_handler(update, None)
        elif update.message and update.message.text:
            command = update.message.text.split()[0]
            handler = COMMAND_HANDLERS.get(command)
            if handler:
                # تمرير None مكان الـ context
                await handler(update, None)
            else:
                # تمرير None مكان الـ context
                await process_message(update, None)

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

if __name__ == "__main__":
    setup_webhook()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
