import logging
import json
import httpx
import asyncio
from datetime import datetime
from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
    PicklePersistence
)
from telegram.error import TelegramError

# --- Configurations ---
# TODO: For better security, load these from environment variables instead of hardcoding.
BOT_TOKEN = "7091291853:AAG_nGI5ZxQVABrkdzwZocf9RIqUcU0tc6g" 
DEEPSEEK_API_KEY = "sk-4f2be0c09b3c4f518a231f7f4b2d793e"  # <--- ضع مفتاح API هنا
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
ADMIN_USER_ID = 7097785684 # <--- ضع معرف حسابك (Admin ID) هنا

DEFAULT_BAN_THRESHOLD = 5
MAX_VIOLATION_LOGS_DISPLAY = 10

# --- Logging Configuration ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- States for Admin Conversation (more readable) ---
AWAITING_BAN_THRESHOLD, AWAITING_BAN_USER, AWAITING_UNBAN_USER, AWAITING_WHITELIST_USER, AWAITING_USER_INFO = range(5)

# --- Utility Functions ---

async def is_admin_or_creator(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """Checks if a user is an admin or creator in a specific chat."""
    if user_id == ADMIN_USER_ID:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except TelegramError as e:
        logger.warning(f"Could not check admin status for user {user_id} in chat {chat_id}: {e}")
        return False

async def has_bot_permissions(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Checks if the bot has the necessary permissions to operate."""
    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
            logger.warning(f"Bot is not an administrator in chat {chat_id}.")
            return False
        if not bot_member.can_delete_messages:
            logger.warning(f"Bot cannot delete messages in chat {chat_id}.")
            return False
        if not bot_member.can_restrict_members:
            logger.warning(f"Bot cannot restrict members in chat {chat_id}.")
            return False
        return True
    except TelegramError as e:
        logger.error(f"Failed to get bot permissions in chat {chat_id}: {e}")
        return False

async def moderate_message_with_ai(message_text: str) -> dict:
    """Analyzes message text using DeepSeek AI for violations."""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    system_prompt = """
    You are a strict digital guardian for Arabic discussion groups. Analyze messages for violations based on the following rules and return a JSON response.
    Rules:
    1. Swearing/insults (including veiled or common offensive Arabic words).
    2. External links/websites (any kind of URL, including shortened ones).
    3. Personal names/private information disclosure.
    4. Inappropriate/indecent content (nudity, explicit material, offensive images/videos).
    5. Rule circumvention, incitement, or spamming.

    Your JSON response MUST strictly follow this format:
    {
        "violation": true/false,
        "reason": "A concise explanation of the violation in Arabic, specifying the rule number if applicable.",
        "rule_number": int
    }
    
    If no violation, 'violation' must be false, 'reason' can be an empty string, and 'rule_number' must be 0.
    """
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message_text}
        ],
        "temperature": 0.1,
        "max_tokens": 200,
        "response_format": {"type": "json_object"}
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            response.raise_for_status()  # Raises an exception for 4XX/5XX responses
            
            result = response.json()
            content = result.get('choices', [{}])[0].get('message', {}).get('content')
            
            if not content:
                raise ValueError("Empty content from AI")
            
            parsed_content = json.loads(content)
            if all(k in parsed_content for k in ["violation", "reason", "rule_number"]):
                return parsed_content
            else:
                raise ValueError("Malformed JSON from AI (missing keys)")

    except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError, ValueError, KeyError, IndexError) as e:
        logger.error(f"DeepSeek AI request failed: {e}")
        return {"violation": False, "reason": "خطأ في الاتصال بخدمة الذكاء الاصطناعي.", "rule_number": 0}

async def delete_and_warn(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str):
    """Deletes the offending message, warns the user, and handles banning."""
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    try:
        await message.delete()
        logger.info(f"Deleted message {message.message_id} from {user.id} in chat {chat.id}.")
    except TelegramError as e:
        logger.error(f"Failed to delete message {message.message_id} in chat {chat.id}: {e}")

    # Persist violation count per user per chat
    violation_key = f"violations_{user.id}_{chat.id}"
    violations = context.chat_data.get(violation_key, 0) + 1
    context.chat_data[violation_key] = violations

    ban_threshold = context.bot_data.get('ban_threshold', DEFAULT_BAN_THRESHOLD)
    user_mention = user.mention_html()

    warning_message = (
        f"⚠️ <b>تحذير للمستخدم</b>: {user_mention}\n"
        f"<b>السبب</b>: {reason}\n"
        f"<b>عدد المخالفات</b>: {violations}/{ban_threshold}"
    )

    if violations >= ban_threshold:
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            warning_message += "\n\n🚫 <b>تم حظر المستخدم لتجاوزه حد المخالفات المسموح به.</b>"
            context.chat_data[violation_key] = 0  # Reset count after ban
            log_message = f"🚫 تم حظر المستخدم {user_mention} ({user.id}) في مجموعة '{chat.title}'."
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=log_message, parse_mode=ParseMode.HTML)
        except TelegramError as e:
            logger.error(f"Failed to ban user {user.id} in chat {chat.id}: {e}")
            warning_message += "\n\n❌ <b>فشل الحظر (تأكد من أن للبوت صلاحية حظر المستخدمين).</b>"

    await context.bot.send_message(
        chat_id=chat.id,
        text=warning_message,
        parse_mode=ParseMode.HTML
    )

    # Log violation for admin review
    violation_logs = context.bot_data.setdefault('violation_logs', [])
    violation_logs.append({
        "user_id": user.id,
        "username": user.full_name,
        "reason": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chat_id": chat.id,
        "chat_title": chat.title
    })
    # Keep logs from getting too large
    context.bot_data['violation_logs'] = violation_logs[-100:]

# --- Message Handlers ---

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main handler for messages in groups."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    if not user or not chat or not message:
        return
        
    # Ignore bots and admins
    if user.is_bot or await is_admin_or_creator(context, chat.id, user.id):
        return

    # Check if moderation is globally disabled
    if not context.bot_data.get('mod_enabled', True):
        return

    # Check if user is on the whitelist
    if user.id in context.bot_data.get('whitelist', []):
        return

    # Ensure bot has permissions before proceeding
    if not await has_bot_permissions(context, chat.id):
        return

    # Determine what to check. Give priority to text/caption.
    text_to_check = message.text or message.caption
    if not text_to_check:
        if message.photo:
            text_to_check = "[رسالة تحتوي على صورة]"
        elif message.video:
            text_to_check = "[رسالة تحتوي على فيديو]"
        elif message.document:
            text_to_check = "[رسالة تحتوي على ملف]"
        else:
            return # No content to moderate

    ai_result = await moderate_message_with_ai(text_to_check)

    if ai_result.get("violation"):
        reason = f"{ai_result.get('reason', 'سبب غير محدد')} (القاعدة #{ai_result.get('rule_number', 'N/A')})"
        await delete_and_warn(update, context, reason)

# --- Admin Panel ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the main admin panel."""
    if update.effective_user.id != ADMIN_USER_ID:
        return

    mod_enabled = context.bot_data.get('mod_enabled', True)
    mod_status_text = "✅ الرقابة مفعلة" if mod_enabled else "❌ الرقابة معطلة"

    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("⚙️ إعدادات الرقابة", callback_data="admin_mod_settings")],
        [InlineKeyboardButton("👤 إدارة المستخدمين", callback_data="admin_manage_users")],
        [InlineKeyboardButton("📝 سجل المخالفات", callback_data="admin_violation_logs")],
        [InlineKeyboardButton(mod_status_text, callback_data="admin_toggle_mod")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🛠️ <b>لوحة تحكم المشرف</b>\n\nأهلاً بك! اختر الإجراء المطلوب من القائمة."

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles all callbacks from the admin panel."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- Actions that don't require user input ---
    if data == "admin_panel":
        await admin_panel(update, context)
        return ConversationHandler.END
    
    if data == "admin_toggle_mod":
        current_status = context.bot_data.get('mod_enabled', True)
        context.bot_data['mod_enabled'] = not current_status
        await query.answer(f"تم {'تعطيل' if current_status else 'تفعيل'} الرقابة بنجاح.", show_alert=True)
        await admin_panel(update, context) # Refresh panel to show new status
        return ConversationHandler.END

    if data == 'admin_violation_logs':
        logs = context.bot_data.get('violation_logs', [])
        text = f"📜 <b>آخر {min(len(logs), MAX_VIOLATION_LOGS_DISPLAY)} مخالفة مسجلة:</b>\n\n"
        if not logs:
            text += "لا توجد مخالفات مسجلة حالياً."
        else:
            for log in reversed(logs[-MAX_VIOLATION_LOGS_DISPLAY:]):
                text += (
                    f"👤 <b>{log['username']}</b> (<code>{log['user_id']}</code>)\n"
                    f"   - <b>السبب:</b> {log['reason']}\n"
                    f"   - <b>المجموعة:</b> {log['chat_title']}\n"
                    f"   - <b>الوقت:</b> {log['timestamp']}\n\n"
                )
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    # --- Actions that require user input (start of conversation) ---
    if data == "admin_set_ban_threshold":
        await query.edit_message_text("🔢 أرسل عدد المخالفات الجديد المطلوب قبل الحظر (مثال: 5).")
        return AWAITING_BAN_THRESHOLD
    if data == "admin_ban_user":
        await query.edit_message_text("⛔ أرسل معرف (ID) المستخدم الذي تريد حظره.")
        return AWAITING_BAN_USER
    if data == "admin_unban_user":
        await query.edit_message_text("✅ أرسل معرف (ID) المستخدم الذي تريد رفع الحظر عنه.")
        return AWAITING_UNBAN_USER
    if data == "admin_add_whitelist":
        await query.edit_message_text("➕ أرسل معرف (ID) المستخدم لإضافته للقائمة البيضاء (سيتم تجاهل رسائله).")
        return AWAITING_WHITELIST_USER
    if data == "admin_get_user_info":
        await query.edit_message_text("🔍 أرسل معرف (ID) المستخدم لعرض معلوماته.")
        return AWAITING_USER_INFO

    return ConversationHandler.END

async def process_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes text input from the admin during a conversation."""
    state = context.user_data.get('state')
    text = update.message.text

    if not text:
        await update.message.reply_text("إدخال غير صالح. يرجى إرسال نص.")
        return state

    # --- Ban Threshold ---
    if state == AWAITING_BAN_THRESHOLD:
        try:
            new_threshold = int(text)
            if new_threshold < 1:
                raise ValueError
            context.bot_data['ban_threshold'] = new_threshold
            await update.message.reply_text(f"✅ تم تحديث حد الحظر إلى {new_threshold} مخالفات.")
        except ValueError:
            await update.message.reply_text("❌ قيمة غير صالحة. يرجى إدخال رقم صحيح أكبر من صفر.")
            return state # Stay in the same state
    
    # --- Ban/Unban/Whitelist/Info by User ID ---
    elif state in [AWAITING_BAN_USER, AWAITING_UNBAN_USER, AWAITING_WHITELIST_USER, AWAITING_USER_INFO]:
        try:
            user_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ معرف المستخدم غير صالح. يرجى إدخال رقم صحيح (ID).")
            return state

        if state == AWAITING_BAN_USER:
            # Note: This is a manual ban from the panel, it does not affect all groups automatically.
            # A full implementation would require iterating over all known chats.
            await update.message.reply_text(f"سيتم تطوير هذه الميزة قريباً. حالياً، الحظر يتم تلقائياً عند تجاوز الحد.")

        elif state == AWAITING_UNBAN_USER:
            await update.message.reply_text(f"سيتم تطوير هذه الميزة قريباً.")

        elif state == AWAITING_WHITELIST_USER:
            whitelist = context.bot_data.setdefault('whitelist', [])
            if user_id not in whitelist:
                whitelist.append(user_id)
                await update.message.reply_text(f"✅ تم إضافة المستخدم <code>{user_id}</code> إلى القائمة البيضاء.", parse_mode=ParseMode.HTML)
            else:
                whitelist.remove(user_id)
                await update.message.reply_text(f"✅ تم إزالة المستخدم <code>{user_id}</code> من القائمة البيضاء.", parse_mode=ParseMode.HTML)
        
        elif state == AWAITING_USER_INFO:
            info_text = f"👤 <b>معلومات المستخدم:</b> <code>{user_id}</code>\n"
            try:
                user_profile = await context.bot.get_chat(user_id)
                info_text += f"<b>الاسم:</b> {user_profile.full_name}\n"
                if user_profile.username:
                    info_text += f"<b>المعرف:</b> @{user_profile.username}\n"
            except TelegramError:
                info_text += "لم أتمكن من جلب تفاصيل المستخدم (قد يكون الحساب محذوفاً أو خاصاً).\n"

            is_whitelisted = user_id in context.bot_data.get('whitelist', [])
            info_text += f"<b>في القائمة البيضاء؟</b> {'نعم' if is_whitelisted else 'لا'}\n"

            # Check violations across all known chats
            total_violations = 0
            for key, value in context.chat_data.items():
                if key.startswith(f"violations_{user_id}_"):
                    total_violations += value
            info_text += f"<b>إجمالي المخالفات المسجلة:</b> {total_violations}"
            await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)

    # End the conversation and show the main panel again
    context.user_data.clear()
    await admin_panel(update, context)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current admin conversation."""
    await update.message.reply_text("تم إلغاء العملية الحالية.")
    context.user_data.clear()
    await admin_panel(update, context)
    return ConversationHandler.END

async def post_init(application: Application):
    """Function to run after the bot is initialized."""
    await application.bot.set_my_commands([
        BotCommand("admin", "فتح لوحة تحكم المشرف"),
        BotCommand("cancel", "إلغاء العملية الحالية"),
    ])
    logger.info("Bot commands have been set.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs errors caused by Updates."""
    logger.error("Exception while handling an update:", exc_info=context.error)

def main():
    """Starts the bot."""
    persistence = PicklePersistence(filepath="bot_data.pkl")
    
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    # Conversation handler for admin inputs
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_admin_callback, pattern="^admin_")
        ],
        states={
            AWAITING_BAN_THRESHOLD: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_input)],
            AWAITING_BAN_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_input)],
            AWAITING_UNBAN_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_input)],
            AWAITING_WHITELIST_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_input)],
            AWAITING_USER_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_input)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CallbackQueryHandler(admin_panel, pattern="^admin_panel$")
        ],
        per_user=True,
        per_chat=False,
    )
    
    # Add handlers
    application.add_handler(CommandHandler("admin", admin_panel, filters.User(ADMIN_USER_ID)))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, handle_group_message))
    application.add_error_handler(error_handler)

    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
