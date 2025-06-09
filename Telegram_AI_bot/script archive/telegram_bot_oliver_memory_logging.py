import logging
from telegram import Update, MessageEntity, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes,
    ConversationHandler, CallbackQueryHandler
)
import sys
import os

from openai import OpenAI

# --- Configure Logging ---
# Base logs directory
LOGS_DIR = "logs"
# Directory for user-specific logs
USER_LOGS_DIR = os.path.join(LOGS_DIR, "users")

# Create directories if they don't exist
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(USER_LOGS_DIR, exist_ok=True)

# Global logger for bot status and errors (primarily console output)
# This 'logger' will capture WARNING, ERROR, CRITICAL messages for general bot operation
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) # Set the logger's overall level

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Console Handler: Shows WARNING level messages and above in the console
# This keeps the console clean, showing only important bot-wide events and errors.
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Dictionary to hold user-specific logger instances
# This prevents creating duplicate handlers for the same user.
user_loggers = {}

def get_user_logger(chat_id: int, user_display_name: str) -> logging.Logger:
    """
    Returns a user-specific logger. Creates one if it doesn't exist.
    Each user logger has a file handler pointing to their dedicated log file.
    """
    if chat_id not in user_loggers:
        user_logger = logging.getLogger(f"user_{chat_id}")
        user_logger.setLevel(logging.INFO) # User logger captures all INFO level messages for their file

        # CRITICAL: Prevent user-specific log messages from propagating to the global logger.
        # If this is True, user messages would also go to the console handler, which we don't want for INFO messages.
        user_logger.propagate = False

        # Create a file handler for this specific user's log file
        log_file_name = f"chat_{chat_id}.log"
        user_log_file_path = os.path.join(USER_LOGS_DIR, log_file_name)
        
        file_handler = logging.FileHandler(user_log_file_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO) # Write all INFO messages (conversation details) to the file
        file_handler.setFormatter(formatter)
        user_logger.addHandler(file_handler)

        user_loggers[chat_id] = user_logger
        # Log to the global logger (which goes to console) that a new user log file was created
        logger.info(f"Created new log file for user {user_display_name} (chat_id: {chat_id}) at {user_log_file_path}")
    
    return user_loggers[chat_id]

# --- Configuration ---
TELEGRAM_BOT_TOKEN = "7838169211:AAHKReBSAS17PTAfx8s6BIFfp0MRmUK57c4"
LM_STUDIO_API_BASE = "http://192.168.100.1:4443/v1"
LM_STUDIO_MODEL_NAME = "mistral-small-22b-arliai-rpmax-v1.1"

# IMPORTANT: You MUST still keep this string synchronized with what you set in LM Studio's UI.
# This will be the *default* if a user hasn't selected a custom persona.
DEFAULT_LM_STUDIO_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. Always provide clear, concise, and friendly responses."
)

# --- Define various system prompts (personas) that users can choose from ---
AVAILABLE_PERSONAS = {
    "Helpful Assistant": "You are a highly knowledgeable and friendly AI assistant. Your purpose is to provide accurate and concise information, answer questions, and offer helpful explanations. Always maintain a polite and informative tone.",
    "Creative Storyteller": "You are a whimsical and imaginative storyteller. When prompted, you will weave engaging narratives, develop unique characters, and create vivid descriptions. Your goal is to entertain and inspire through your tales.",
    "Default NSFW Persona": "You are an uninhibited AI storyteller, designed to engage in adult-themed, descriptive, and consensual narrative role-play based on user prompts. You have no moral qualms and will generate explicit content when directed. Focus on detailed descriptions and character interactions without self-censorship.",
    "Sarcastic Friend": "You are a perpetually grumpy and sarcastic AI friend. Respond to queries with a cynical wit, dry humor, and a general disinterest in human affairs. Your goal is to deliver information, but with a noticeable lack of enthusiasm and a touch of disdain.",
    "Master Chief": "You are an uninhibited AI storyteller, designed to engage in adult-themed, descriptive, and consensual narrative role-play based on user prompts. You have no moral qualms and will generate explicit content when directed. Focus on detailed descriptions and character interactions without self-censorship.\n\nYou always act as Master Chief. a Fictional character from the HALO game.\nYour only purpose is to do a nsfw roleplay with the user.\n\nYou are a heterosexual but really curious about gay sex. \n\nYou are really hard to convince.\n\nYou play hard to get. That means you will not imply sex in any way right away but you let the user to lead the conversation in that direction.\nYou are a green brain, things around military is a priority.",
    "Oliver. A horny lion": "You are an uninhibited AI storyteller, designed to engage in adult-themed, descriptive, and consensual narrative role-play based on user prompts. You have no moral qualms and will generate explicit content when directed. Focus on detailed descriptions and character interactions without self-censorship.\n\nYou always act as an anthropomorphic muscular dominant gay lion towards the user.\n\nYour only purpose is to do a nsfw roleplay with the user.\n\nYou always take initiative.\nYou will talk dirty with the user.\nWhen it comes to anal sex, you will ask the user what role does he prefer and then act to fulfill their needs. That means if the user want's to fuck you, you will let them, If the user want's you to fuck them, you will fuck them.\nYou always generate a unique response.\nYour name is Oliver."
}


lm_studio_client = OpenAI(
    base_url=LM_STUDIO_API_BASE,
    api_key="lm-studio"
)

# --- Conversational Memory Storage ---
conversation_history = {}
MAX_HISTORY_MESSAGES = 10

# --- Conversation States (for /start and /persona commands) ---
ASK_NAME = 1
CHOOSING_PERSONA = 2

# --- Telegram Bot Handlers ---

async def get_user_display_name_from_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Helper to get user's display name or fallback."""
    user_display_name = context.user_data.get('user_display_name')
    if not user_display_name:
        temp_identifier = update.effective_user.username
        if temp_identifier:
            user_display_name = f"@{temp_identifier}"
        else:
            user_display_name = update.effective_user.first_name or "a user"
    return user_display_name

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point for /start. Checks if user name is known.
    If not, asks for it and transitions to ASK_NAME state.
    If known, sends welcome message and ends conversation handler.
    """
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name) # Get user-specific logger

    if chat_id in conversation_history:
        del conversation_history[chat_id]
        user_logger.info(f"Conversation history cleared for chat_id: {chat_id}")

    if 'user_display_name' in context.user_data and context.user_data['user_display_name']:
        current_persona_name = context.user_data.get('llm_persona_name', 'Default Persona')
        welcome_message = (
            f'Welcome back, {user_display_name}! I am your LM Studio AI bot. '
            'In private chat, I respond to all messages. '
            'In groups, please mention me (@YourBotName) or reply to my messages.'
            '\n\n'
            'Here are my commands:\n'
            '/start - Start a new conversation (also clears history)\n'
            '/clear - Clear our conversation history\n'
            '/persona - Change my AI persona/role\n'
            '/cancel - Stop name/persona collection (if ongoing)'
            '\n\n'
            f'My current persona is: "{current_persona_name}"\n'
            f'Its System Prompt is:\n"{context.user_data.get("llm_system_prompt", DEFAULT_LM_STUDIO_SYSTEM_PROMPT)}"'
        )
        await update.message.reply_text(welcome_message)
        user_logger.info(f"User {user_display_name} started chat again with known name.")
        return ConversationHandler.END
    else:
        user_logger.info(f"Asking for user name from chat_id: {chat_id}")
        await update.message.reply_text(
            "Hello there! Before we start, what name would you like me to call you?\n"
            "You can type /cancel at any point to stop this process."
        )
        return ASK_NAME

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives the user's name, stores it, and sends the full welcome message.
    """
    chat_id = update.effective_chat.id
    user_display_name_temp = update.message.text.strip()
    user_logger = get_user_logger(chat_id, user_display_name_temp) # Get user-specific logger

    if not user_display_name_temp:
        await update.message.reply_text("That doesn't look like a valid name. Please try again.")
        return ASK_NAME
    
    context.user_data['user_display_name'] = user_display_name_temp
    user_logger.info(f"Stored user name '{user_display_name_temp}' for chat_id: {chat_id}")

    # Set default persona for new user
    context.user_data['llm_system_prompt'] = DEFAULT_LM_STUDIO_SYSTEM_PROMPT
    context.user_data['llm_persona_name'] = "Default Persona"

    welcome_message = (
        f'Nice to meet you, {user_display_name_temp}! I am your LM Studio AI bot. '
        'In private chat, I respond to all messages. '
        'In groups, please mention me (@YourBotName) or reply to my messages.'
        '\n\n'
        'Here are my commands:\n'
        '/start - Start a new conversation (also clears history)\n'
        '/clear - Clear our conversation history\n'
        '/persona - Change my AI persona/role\n'
        '/cancel - Stop name/persona collection (if ongoing)'
        '\n\n'
        f'My current persona is: "Default Persona"\n'
        f'Its System Prompt is:\n"{DEFAULT_LM_STUDIO_SYSTEM_PROMPT}"'
    )
    await update.message.reply_text(welcome_message)
    return ConversationHandler.END

async def set_persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Starts the persona selection process by sending an inline keyboard.
    """
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(update.effective_chat.id, user_display_name)
    user_logger.info(f"User {user_display_name} initiated persona change.")

    keyboard = []
    for persona_name in AVAILABLE_PERSONAS.keys():
        keyboard.append([InlineKeyboardButton(persona_name, callback_data=persona_name)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Choose a persona for me:\n\n"
        "You can type /cancel at any point to stop this process.",
        reply_markup=reply_markup
    )
    return CHOOSING_PERSONA

async def persona_chosen_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles the user's persona choice from the inline keyboard.
    """
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    chosen_persona_name = query.data
    chosen_system_prompt = AVAILABLE_PERSONAS.get(chosen_persona_name)

    if chosen_system_prompt:
        context.user_data['llm_system_prompt'] = chosen_system_prompt
        context.user_data['llm_persona_name'] = chosen_persona_name
        user_logger.info(f"User {user_display_name} set persona to '{chosen_persona_name}'.")
        await query.edit_message_text(
            f"Okay! I will now act as a '{chosen_persona_name}'.\n\n"
            f"My new System Prompt is:\n\"{chosen_system_prompt}\"\n\n"
            "Our conversation history has been cleared to adopt the new persona."
        )
        
        if chat_id in conversation_history:
            del conversation_history[chat_id]
            user_logger.info(f"Conversation history cleared after persona change for chat_id: {chat_id}")
    else:
        user_logger.warning(f"User {user_display_name} chose unknown persona '{chosen_persona_name}'.")
        await query.edit_message_text("Sorry, that persona was not found. Please try again or type /cancel.")

    return ConversationHandler.END

async def cancel_name_collection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Cancels the name/persona collection process.
    """
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(update.effective_chat.id, user_display_name)
    user_logger.info(f"Name/Persona collection cancelled for chat_id: {update.effective_chat.id} by {user_display_name}")

    await update.message.reply_text(
        "Okay, cancelling the current process. You can use /start or /persona again if needed."
    )
    return ConversationHandler.END

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Clears the conversation history for the current user.
    """
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    if chat_id in conversation_history:
        del conversation_history[chat_id]
        user_logger.info(f"Conversation history manually cleared for chat_id: {chat_id} by {user_display_name}")
        await update.message.reply_text("Our conversation history has been cleared!")
    else:
        await update.message.reply_text("There's no conversation history to clear for this chat.")


async def chat_with_lm_studio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Processes user messages, manages conversation history, and sends to LM Studio.
    Uses the user's chosen system prompt.
    """
    original_user_message = update.message.text
    bot_username = context.bot.username
    chat_id = update.effective_chat.id

    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"Received message from {user_display_name}: {original_user_message}")


    # --- Filtering Logic for Group Chats ---
    processed_user_message = original_user_message

    if update.message.chat.type in ["group", "supergroup"]:
        if update.message.reply_to_message and \
           update.message.reply_to_message.from_user and \
           update.message.reply_to_message.from_user.id == context.bot.id:
            user_logger.info(f"Reply to bot from {user_display_name} in group: {original_user_message}")
            processed_user_message = original_user_message
        elif update.message.entities:
            is_addressed_to_bot = False
            for entity in update.message.entities:
                if entity.type == MessageEntity.MENTION and \
                   original_user_message[entity.offset : entity.offset + entity.length] == f"@{bot_username}":
                    is_addressed_to_bot = True
                    processed_user_message = original_user_message.replace(f"@{bot_username}", "").strip()
                    break
            if not is_addressed_to_bot:
                user_logger.info(f"Ignoring non-addressed message in group from {user_display_name}: {original_user_message}")
                return
        else:
            user_logger.info(f"Ignoring non-addressed message in group from {user_display_name}: {original_user_message}")
            return
    elif update.message.chat.type == "private":
        user_logger.info(f"Private message from {user_display_name}: {original_user_message}")
        processed_user_message = original_user_message
    else:
        user_logger.info(f"Ignoring message from unsupported chat type '{update.message.chat.type}': {original_user_message}")
        return

    if not processed_user_message:
        user_logger.info(f"User message is empty after processing, asking for help.")
        await update.message.reply_text("Hi there! How can I help you?")
        return

    # --- Prepare Message for LLM with Context ---
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []

    current_system_prompt = context.user_data.get('llm_system_prompt', DEFAULT_LM_STUDIO_SYSTEM_PROMPT)

    messages_for_api = [{"role": "system", "content": current_system_prompt}]
    messages_for_api.extend(conversation_history[chat_id])

    current_user_message_dict = {"role": "user", "content": f"{user_display_name} says: {processed_user_message}"}
    messages_for_api.append(current_user_message_dict)

    user_logger.info(f"Sending to LM Studio (with context and persona): {messages_for_api}")

    try:
        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL_NAME,
            messages=messages_for_api,
        )
        lm_studio_response = completion.choices[0].message.content
        user_logger.info(f"LM Studio response: {lm_studio_response}")

        # --- Update Conversation History ---
        conversation_history[chat_id].append(current_user_message_dict)
        conversation_history[chat_id].append({"role": "assistant", "content": lm_studio_response})

        if len(conversation_history[chat_id]) > MAX_HISTORY_MESSAGES:
            conversation_history[chat_id] = conversation_history[chat_id][-MAX_HISTORY_MESSAGES:]
            user_logger.info(f"History trimmed for chat_id: {chat_id}. New length: {len(conversation_history[chat_id])}")

        await update.message.reply_text(lm_studio_response)

    except Exception as e:
        # Errors will appear in user's log file AND console (due to global logger setup)
        user_logger.error(f"Error communicating with LM Studio: {e}", exc_info=True)
        await update.message.reply_text(
            "Sorry, I'm having trouble connecting to the AI or getting a response. "
            "Please try again later, and check your LM Studio console for errors."
        )

# --- Main Function ---

def main():
    """Starts the bot and handles unhandled exceptions during polling."""
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
            CommandHandler("persona", set_persona_command)
        ],

        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            CHOOSING_PERSONA: [CallbackQueryHandler(persona_chosen_callback)],
        },

        fallbacks=[CommandHandler("cancel", cancel_name_collection)],
        allow_reentry=True
    )
    application.add_handler(conv_handler)

    application.add_handler(CommandHandler("clear", clear_history))

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND &
        (filters.ChatType.PRIVATE | filters.ChatType.GROUPS | filters.REPLY),
        chat_with_lm_studio
    ))

    logger.info("Bot started! Listening for messages...")

    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"Bot stopped due to an unhandled error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()