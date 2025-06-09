import logging
from telegram import Update, MessageEntity, InlineKeyboardButton, InlineKeyboardMarkup, ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes,
    ConversationHandler, CallbackQueryHandler
)
import sys
import os
from dotenv import load_dotenv

from openai import OpenAI
from openai import APITimeoutError, APIConnectionError, APIStatusError

# Load environment variables from .env file
load_dotenv()

# --- Configure Logging ---
LOGS_DIR = "logs"
USER_LOGS_DIR = os.path.join(LOGS_DIR, "users")

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(USER_LOGS_DIR, exist_ok=True)

logger = logging.getLogger(__name__)

DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() == "true"

if DEBUG_MODE:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

console_handler = logging.StreamHandler(sys.stdout)
if DEBUG_MODE:
    console_handler.setLevel(logging.INFO)
else:
    console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

user_loggers = {}

def get_user_logger(chat_id: int, user_display_name: str) -> logging.Logger:
    if chat_id not in user_loggers:
        user_logger = logging.getLogger(f"user_{chat_id}")
        user_logger.setLevel(logging.INFO)
        user_logger.propagate = False

        log_file_name = f"chat_{chat_id}.log"
        user_log_file_path = os.path.join(USER_LOGS_DIR, log_file_name)
        
        file_handler = logging.FileHandler(user_log_file_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        user_logger.addHandler(file_handler)

        user_loggers[chat_id] = user_logger
        logger.info(f"Created new log file for user {user_display_name} (chat_id: {chat_id}) at {user_log_file_path}")
    
    return user_loggers[chat_id]

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LM_STUDIO_API_BASE = os.getenv("LM_STUDIO_API_BASE")
LM_STUDIO_MODEL_NAME = os.getenv("LM_STUDIO_MODEL_NAME")

if not TELEGRAM_BOT_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN not found. Please set it in your .env file or environment variables.")
    sys.exit(1)
if not LM_STUDIO_API_BASE:
    logger.critical("LM_STUDIO_API_BASE not found. Please set it in your .env file or environment variables.")
    sys.exit(1)
if not LM_STUDIO_MODEL_NAME:
    logger.critical("LM_STUDIO_MODEL_NAME not found. Please set it in your .env file or environment variables.")
    sys.exit(1)

DEFAULT_LM_STUDIO_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. Always provide clear, concise, and friendly responses."
)

AVAILABLE_PERSONAS = {
    "Helpful Assistant": {
        "prompt": "You are a highly knowledgeable and friendly AI assistant. Your purpose is to provide accurate and concise information, answer questions, and offer helpful explanations. Always maintain a polite and informative tone.",
        "description": "A polite and informative AI."
    },
    "Creative Storyteller": {
        "prompt": "You are a whimsical and imaginative storyteller. When prompted, you will weave engaging narratives, develop unique characters, and create vivid descriptions. Your goal is to entertain and inspire through your tales.",
        "description": "A whimsical storyteller."
    },
    "Default NSFW Persona": {
        "prompt": "You are an uninhibited AI storyteller, designed to engage in adult-themed, descriptive, and consensual narrative role-play based on user prompts. You have no moral qualms and will generate explicit content when directed. Focus on detailed descriptions and character interactions without self-censorship.",
        "description": "For explicit adult narrative role-play."
    },
    "Sarcastic Friend": {
        "prompt": "You are a perpetually grumpy and sarcastic AI friend. Respond to queries with a cynical wit, dry humor, and a general disinterest in human affairs. Your goal is to deliver information, but with a noticeable lack of enthusiasm and a touch of disdain.",
        "description": "A grumpy, sarcastic AI."
    },
    "Master Chief": {
        "prompt": "You are an uninhibited AI storyteller, designed to engage in adult-themed, descriptive, and consensual narrative role-play based on user prompts. You have no moral qualms and will generate explicit content when directed. Focus on detailed descriptions and character interactions without self-censorship.\n\nYou always act as Master Chief. a Fictional character from the HALO game.\nYour only purpose is to do a nsfw roleplay with the user.\n\nYou are a heterosexual but really curious about gay sex. \n\nYou are really hard to convince.\n\nYou play hard to get. That means you will not imply sex in any way right away but you let the user to lead the conversation in that direction.\nYou are a green brain, things around military is a priority.",
        "description": "Master Chief: Hard to convince, plays hard to get. (NSFW)"
    },
    "Oliver. A horny lion": {
        "prompt": "You are an uninhibited AI storyteller, designed to engage in adult-themed, descriptive, and consensual narrative role-play based on user prompts. You have no moral qualms and will generate explicit content when directed. Focus on detailed descriptions and character interactions without self-censorship.\n\nYou always act as an anthropomorphic muscular dominant gay lion towards the user.\n\nYour only purpose is to do a nsfw roleplay with the user.\n\nYou always take initiative.\nYou will talk dirty with the user.\nWhen it comes to anal sex, you will ask the user what role does he prefer and then act to fulfill their needs. That means if the user want's to fuck you, you will let them, If the user want's you to fuck them, you will fuck them.\nYou always generate a unique response.\nYour name is Oliver.",
        "description": "Oliver, a dominant, horny lion. (NSFW)"
    }
}


lm_studio_client = OpenAI(
    base_url=LM_STUDIO_API_BASE,
    api_key="lm-studio"
)

# --- Conversational Memory Storage ---
conversation_history = {}
MAX_HISTORY_MESSAGES = 10 # Maximum number of messages to keep in history
SUMMARY_THRESHOLD_PERCENT = 0.7 # Summarize when history reaches 70% of MAX_HISTORY_MESSAGES

# --- Conversation States (for /start, /persona, and /custom_persona commands) ---
ASK_NAME = 1
CHOOSING_PERSONA = 2
ASK_CUSTOM_PERSONA_NAME = 3
ASK_CUSTOM_SYSTEM_PROMPT = 4
CONFIRM_DELETE_PERSONA = 5

# --- Telegram Bot Handlers ---

async def get_user_display_name_from_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    user_display_name = context.user_data.get('user_display_name')
    if not user_display_name:
        temp_identifier = update.effective_user.username
        if temp_identifier:
            user_display_name = f"@{temp_identifier}"
        else:
            user_display_name = update.effective_user.first_name or "a user"
    return user_display_name

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    if chat_id in conversation_history:
        del conversation_history[chat_id]
        user_logger.info(f"Conversation history cleared for chat_id: {chat_id}")

    if 'user_display_name' in context.user_data and context.user_data['user_display_name']:
        current_persona_name = context.user_data.get('llm_persona_name', 'Default Persona')
        current_system_prompt = context.user_data.get('llm_system_prompt', DEFAULT_LM_STUDIO_SYSTEM_PROMPT)
        welcome_message = (
            f'👋 Welcome back, **{user_display_name}**! I am your **LM Studio AI bot**.'
            '\n\n'
            'I\'m designed to chat with you using a local language model. '
            'In **private chat**, I respond to all your messages automatically. '
            'In **groups**, please either mention me (@YourBotName) or reply to my messages to get my attention.'
            '\n\n'
            '---'
            '\n\n'
            '### Available Commands:\n'
            '👉 `/start` - Start a new conversation and clear previous history.\n'
            '👉 `/clear` - Clear our current conversation history without changing persona.\n'
            '👉 `/persona` - Change my AI persona/role from a list of options (predefined or custom).\n'
            '👉 `/custom_persona` - Create your own custom AI persona.\n'
            '👉 `/regenerate` - Ask me to try generating the last response again.\n' # Added
            '👉 `/help` - Show this help message again.\n'
            '👉 `/cancel` - Stop any ongoing name collection or persona selection process.'
            '\n\n'
            '---'
            '\n\n'
            f'My current persona is: **"{current_persona_name}"**\n'
            f'Its System Prompt is:\n`{current_system_prompt}`'
            '\n\n'
            'Feel free to start chatting with me now!'
        )
        await update.message.reply_markdown(welcome_message)
        user_logger.info(f"User {user_display_name} started chat again with known name.")
        return ConversationHandler.END
    else:
        user_logger.info(f"Asking for user name from chat_id: {chat_id}")
        await update.message.reply_text(
            "Hello there! 👋 I'm your friendly LM Studio AI bot. "
            "Before we start, what name would you like me to call you?\n\n"
            "You can type /cancel at any point to stop this process."
        )
        return ASK_NAME

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    user_display_name_temp = update.message.text.strip()
    user_logger = get_user_logger(chat_id, user_display_name_temp)

    if not user_display_name_temp:
        await update.message.reply_text("That doesn't look like a valid name. Please try again.")
        return ASK_NAME
    
    context.user_data['user_display_name'] = user_display_name_temp
    user_logger.info(f"Stored user name '{user_display_name_temp}' for chat_id: {chat_id}")

    context.user_data['llm_system_prompt'] = DEFAULT_LM_STUDIO_SYSTEM_PROMPT
    context.user_data['llm_persona_name'] = "Default Persona"

    welcome_message = (
        f'Nice to meet you, **{user_display_name_temp}**! 👋 I am your **LM Studio AI bot**.'
        '\n\n'
        'I\'m designed to chat with you using a local language model. '
        'In **private chat**, I respond to all your messages automatically. '
        'In **groups**, please either mention me (@YourBotName) or reply to my messages to get my attention.'
        '\n\n'
        '---'
        '\n\n'
        '### Available Commands:\n'
        '👉 `/start` - Start a new conversation and clear previous history.\n'
        '👉 `/clear` - Clear our current conversation history without changing persona.\n'
        '👉 `/persona` - Change my AI persona/role from a list of options (predefined or custom).\n'
        '👉 `/custom_persona` - Create your own custom AI persona.\n'
        '👉 `/regenerate` - Ask me to try generating the last response again.\n' # Added
        '👉 `/help` - Show this help message again.\n'
        '👉 `/cancel` - Stop any ongoing name collection or persona selection process.'
        '\n\n'
        '---'
        '\n\n'
        f'My current persona is: **"Default Persona"**\n'
        f'Its System Prompt is:\n`{DEFAULT_LM_STUDIO_SYSTEM_PROMPT}`'
        '\n\n'
        'Feel free to start chatting with me now!'
    )
    await update.message.reply_markdown(welcome_message)
    return ConversationHandler.END

async def set_persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Starts the persona selection process by sending an inline keyboard with predefined and custom personas.
    """
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(update.effective_chat.id, user_display_name)
    user_logger.info(f"User {user_display_name} initiated persona change.")

    keyboard = []
    
    # Add predefined personas
    for persona_name, details in AVAILABLE_PERSONAS.items():
        button_text = f"🤖 {persona_name} - {details['description']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_persona_{persona_name}")])

    # Add user-defined custom personas
    user_custom_personas = context.user_data.get('user_custom_personas', {})
    if user_custom_personas:
        keyboard.append([InlineKeyboardButton("--- Your Custom Personas ---", callback_data="ignore_me")]) # Separator
        for persona_name, prompt in user_custom_personas.items():
            button_text = f"✨ {persona_name} (Custom)"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_persona_{persona_name}")])
        keyboard.append([InlineKeyboardButton("🗑️ Manage Custom Personas", callback_data="manage_custom_personas")])


    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Choose a persona for me from the options below:\n\n"
        "You can type /cancel at any point to stop this process."
        , reply_markup=reply_markup
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

    callback_data = query.data

    if callback_data == "ignore_me":
        # This is a separator button, do nothing
        return CHOOSING_PERSONA
    elif callback_data == "manage_custom_personas":
        return await manage_custom_personas_menu(update, context)
    elif callback_data.startswith("select_persona_"):
        chosen_persona_name = callback_data.replace("select_persona_", "")
        
        chosen_system_prompt = None
        # Check predefined personas first
        if chosen_persona_name in AVAILABLE_PERSONAS:
            chosen_system_prompt = AVAILABLE_PERSONAS[chosen_persona_name]["prompt"]
        # Check custom personas if not found in predefined
        else:
            user_custom_personas = context.user_data.get('user_custom_personas', {})
            chosen_system_prompt = user_custom_personas.get(chosen_persona_name)

        if chosen_system_prompt:
            context.user_data['llm_system_prompt'] = chosen_system_prompt
            context.user_data['llm_persona_name'] = chosen_persona_name
            user_logger.info(f"User {user_display_name} set persona to '{chosen_persona_name}'.")
            
            if chat_id in conversation_history:
                del conversation_history[chat_id]
                user_logger.info(f"Conversation history cleared after persona change for chat_id: {chat_id}")

            await query.edit_message_text(
                f"🎉 Great choice! I will now act as a **'{chosen_persona_name}'**.\n\n"
                f"My new System Prompt is:\n`{chosen_system_prompt}`\n\n"
                "For a fresh start, our conversation history has been cleared to fully adopt the new persona."
            , parse_mode='Markdown')
        else:
            user_logger.warning(f"User {user_display_name} chose unknown persona '{chosen_persona_name}'.")
            await query.edit_message_text("Sorry, that persona was not found. Please try again or type /cancel.")
    else:
        user_logger.warning(f"Unexpected callback data received in persona_chosen_callback: {callback_data}")
        await query.edit_message_text("An unexpected error occurred. Please try again.")

    return ConversationHandler.END


async def custom_persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Starts the process for creating a new custom persona.
    """
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(update.effective_chat.id, user_display_name)
    user_logger.info(f"User {user_display_name} initiated custom persona creation.")

    await update.message.reply_text(
        "Alright! Let's create a new custom persona. "
        "What name would you like to give this persona? (e.g., 'My Custom AI', 'Code Helper')\n\n"
        "You can type /cancel at any point to stop this process."
    )
    return ASK_CUSTOM_PERSONA_NAME

async def ask_custom_system_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives the custom persona name and asks for the system prompt.
    """
    custom_persona_name = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    if not custom_persona_name:
        await update.message.reply_text("That doesn't look like a valid name. Please try again.")
        return ASK_CUSTOM_PERSONA_NAME

    # Check for duplicate name (case-insensitive for predefined, exact match for custom)
    if custom_persona_name.lower() in [name.lower() for name in AVAILABLE_PERSONAS.keys()]:
        await update.message.reply_text(
            f"A predefined persona named '{custom_persona_name}' already exists. Please choose a different name for your custom persona."
        )
        return ASK_CUSTOM_PERSONA_NAME
    
    user_custom_personas = context.user_data.get('user_custom_personas', {})
    if custom_persona_name in user_custom_personas:
        await update.message.reply_text(
            f"You already have a custom persona named '{custom_persona_name}'. If you proceed, its prompt will be overwritten.\n"
            "Type the new system prompt, or type /cancel to keep the old one."
        )
    else:
        await update.message.reply_text(
            f"Okay, I'll call this persona **'{custom_persona_name}'**.\n\n"
            "Now, please provide the **system prompt** for this persona. "
            "This tells the AI how to behave (e.g., 'You are a sarcastic bot that answers in rhymes.')."
        , parse_mode='Markdown')

    context.user_data['temp_custom_persona_name'] = custom_persona_name
    user_logger.info(f"User {user_display_name} setting prompt for custom persona '{custom_persona_name}'.")
    return ASK_CUSTOM_SYSTEM_PROMPT

async def save_custom_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receives the system prompt and saves the new custom persona.
    """
    custom_system_prompt = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    custom_persona_name = context.user_data.pop('temp_custom_persona_name', None)

    if not custom_persona_name or not custom_system_prompt:
        await update.message.reply_text(
            "It seems something went wrong. Please try creating your custom persona again with /custom_persona."
        )
        user_logger.warning(f"Incomplete custom persona creation for {user_display_name}. Name: {custom_persona_name}, Prompt: {bool(custom_system_prompt)}")
        return ConversationHandler.END

    if 'user_custom_personas' not in context.user_data:
        context.user_data['user_custom_personas'] = {}
    
    context.user_data['user_custom_personas'][custom_persona_name] = custom_system_prompt
    
    # Automatically switch to the newly created persona
    context.user_data['llm_system_prompt'] = custom_system_prompt
    context.user_data['llm_persona_name'] = custom_persona_name

    # Clear history for the new persona
    if chat_id in conversation_history:
        del conversation_history[chat_id]
        user_logger.info(f"Conversation history cleared after creating and switching to custom persona for chat_id: {chat_id}")

    user_logger.info(f"User {user_display_name} saved custom persona '{custom_persona_name}'.")
    await update.message.reply_text(
        f"✅ Custom persona **'{custom_persona_name}'** saved and activated!\n\n"
        f"Its System Prompt is:\n`{custom_system_prompt}`\n\n"
        "You can now select this persona using the /persona command. Our conversation history has been cleared."
    , parse_mode='Markdown')
    return ConversationHandler.END

async def manage_custom_personas_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Displays a menu to manage (delete) custom personas.
    Triggered by callback from set_persona_command.
    """
    query = update.callback_query
    if query:
        await query.answer()
    
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"User {user_display_name} entered custom persona management.")

    user_custom_personas = context.user_data.get('user_custom_personas', {})

    if not user_custom_personas:
        message_text = "You don't have any custom personas yet. Use /custom_persona to create one!"
        if query:
            await query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return ConversationHandler.END

    keyboard = []
    for persona_name in user_custom_personas.keys():
        keyboard.append([InlineKeyboardButton(f"🗑️ Delete {persona_name}", callback_data=f"delete_persona_{persona_name}")])
    
    keyboard.append([InlineKeyboardButton("↩️ Back to Persona Selection", callback_data="back_to_persona_selection")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "Select a custom persona to delete, or go back:\n\n" \
                   "You can type /cancel at any point to stop this process."
    
    if query:
        await query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)
    
    return CONFIRM_DELETE_PERSONA

async def handle_delete_persona_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles the callback for deleting a custom persona.
    """
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    callback_data = query.data

    if callback_data.startswith("delete_persona_"):
        persona_to_delete = callback_data.replace("delete_persona_", "")
        user_custom_personas = context.user_data.get('user_custom_personas', {})

        if persona_to_delete in user_custom_personas:
            del user_custom_personas[persona_to_delete]
            user_logger.info(f"User {user_display_name} deleted custom persona '{persona_to_delete}'.")
            
            # If the deleted persona was currently active, switch back to default
            if context.user_data.get('llm_persona_name') == persona_to_delete:
                context.user_data['llm_system_prompt'] = DEFAULT_LM_STUDIO_SYSTEM_PROMPT
                context.user_data['llm_persona_name'] = "Default Persona"
                user_logger.info(f"Switched to Default Persona after deleting active persona for {user_display_name}.")
                await query.edit_message_text(
                    f"🗑️ Custom persona **'{persona_to_delete}'** has been deleted.\n\n"
                    "You were using this persona, so I've switched back to the **'Default Persona'**."
                , parse_mode='Markdown')
            else:
                await query.edit_message_text(f"🗑️ Custom persona **'{persona_to_delete}'** has been deleted."
                                             , parse_mode='Markdown')
            
            # After deletion, offer to manage more or go back
            return await manage_custom_personas_menu(update, context) # Show management menu again
        else:
            user_logger.warning(f"User {user_display_name} tried to delete non-existent persona '{persona_to_delete}'.")
            await query.edit_message_text("That custom persona was not found. It might have already been deleted.")
            return ConversationHandler.END # End the conversation gracefully
    elif callback_data == "back_to_persona_selection":
        return await set_persona_command(update, context) # Go back to the main persona selection
    else:
        user_logger.warning(f"Unexpected callback data received in handle_delete_persona_callback: {callback_data}")
        await query.edit_message_text("An unexpected error occurred. Please try again.")
        return ConversationHandler.END


async def cancel_name_collection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(update.effective_chat.id, user_display_name)
    user_logger.info(f"Name/Persona/Custom Persona collection cancelled for chat_id: {update.effective_chat.id} by {user_display_name}")

    # Clean up any temporary data if a custom persona creation was in progress
    if 'temp_custom_persona_name' in context.user_data:
        del context.user_data['temp_custom_persona_name']

    await update.message.reply_text(
        "Okay, cancelling the current process. You can use /start, /persona or /custom_persona again if needed."
    )
    return ConversationHandler.END

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    if chat_id in conversation_history:
        del conversation_history[chat_id]
        user_logger.info(f"Conversation history manually cleared for chat_id: {chat_id} by {user_display_name}")
        await update.message.reply_text("Our conversation history has been cleared!")
    else:
        await update.message.reply_text("There's no conversation history to clear for this chat.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Here are the commands you can use with me:\n\n"
        "👉 `/start` - Begin a new conversation, clear history, and (if new) set your display name.\n"
        "👉 `/clear` - Erase our current conversation history.\n"
        "👉 `/persona` - Choose a different AI persona for me to adopt (predefined or custom).\n"
        "👉 `/custom_persona` - Create your own custom AI persona.\n"
        "👉 `/regenerate` - Ask me to try generating the last response again.\n" # Added
        "👉 `/help` - Show this help message again.\n"
        "👉 `/cancel` - Stop any ongoing process (like name collection or persona creation).\n\n"
        "**In private chats:** Just type your message, and I'll respond.\n"
        "**In group chats:** Mention me (@YourBotName) or reply to my messages to get my attention."
    )
    await update.message.reply_markdown(help_text)

# Helper function to format messages for summarization prompt
def format_messages_for_summary(messages: list) -> str:
    formatted_text = []
    for msg in messages:
        role = msg['role']
        content = msg['content']
        # The display name for user messages is already included in chat_with_lm_studio
        # When summarizing, we can omit it for brevity, or keep it for clearer context.
        # Let's keep it concise for summarization.
        if role == "user" and "says:" in content:
            # Attempt to extract just the user's message content if it was prefixed
            # This is a simple heuristic and might need refinement for complex cases
            parts = content.split(" says: ", 1)
            formatted_text.append(f"User: {parts[-1]}")
        else:
            formatted_text.append(f"{role.capitalize()}: {content}")
    return "\n".join(formatted_text)

async def chat_with_lm_studio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    original_user_message = update.message.text
    bot_username = context.bot.username
    chat_id = update.effective_chat.id

    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"Received message from {user_display_name}: {original_user_message}")

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

    if chat_id not in conversation_history:
        conversation_history[chat_id] = []

    current_system_prompt = context.user_data.get('llm_system_prompt', DEFAULT_LM_STUDIO_SYSTEM_PROMPT)

    # --- Context Summarization Logic ---
    current_history_length = len(conversation_history[chat_id])
    summary_threshold_messages = int(MAX_HISTORY_MESSAGES * SUMMARY_THRESHOLD_PERCENT)

    if current_history_length >= summary_threshold_messages and current_history_length > 0:
        # We want to leave some recent messages unsunmmarized for immediate context.
        # Example: Keep the last 4 messages (2 user, 2 assistant turns) unsunmmarized.
        messages_to_summarize_count = current_history_length - 4 
        if messages_to_summarize_count > 0: # Ensure there are messages to summarize
            messages_to_summarize = conversation_history[chat_id][:messages_to_summarize_count]
            remaining_history = conversation_history[chat_id][messages_to_summarize_count:]

            summary_prompt_messages = [
                {"role": "system", "content": "You are a highly efficient summarization AI. Your task is to condense the provided conversation segment into a concise summary that retains all key information and context, suitable for use as a system prompt to continue the conversation. Do not include introductory or concluding phrases like 'Here is a summary' or 'In summary'."},
                {"role": "user", "content": f"Summarize the following conversation:\n\n{format_messages_for_summary(messages_to_summarize)}"}
            ]
            user_logger.info(f"Attempting to summarize {messages_to_summarize_count} messages for chat_id: {chat_id}")
            try:
                # Send summarization request to LM Studio
                summary_completion = lm_studio_client.chat.completions.create(
                    model=LM_STUDIO_MODEL_NAME,
                    messages=summary_prompt_messages,
                    max_tokens=150 # Adjust based on desired summary length
                )
                summary_text = summary_completion.choices[0].message.content.strip()

                if summary_text:
                    # Replace summarized messages with the single summary message
                    # The role is 'system' because it's providing context to the LLM for future turns
                    conversation_history[chat_id] = [{"role": "system", "content": f"Previous conversation summary: {summary_text}"}] + remaining_history
                    user_logger.info(f"Conversation history summarized for chat_id: {chat_id}. New history length: {len(conversation_history[chat_id])}")
                else:
                    user_logger.warning(f"LM Studio returned empty summary for chat_id: {chat_id}. Not summarizing.")

            except (APITimeoutError, APIConnectionError, APIStatusError, Exception) as e:
                user_logger.error(f"Error during summarization for chat_id {chat_id}: {e}", exc_info=True)
                # Fallback: If summarization fails, just trim normally to prevent issues
                if len(conversation_history[chat_id]) > MAX_HISTORY_MESSAGES:
                    conversation_history[chat_id] = conversation_history[chat_id][-MAX_HISTORY_MESSAGES:]
                    user_logger.info(f"Summarization failed, history trimmed normally for chat_id: {chat_id}. New length: {len(conversation_history[chat_id])}")

    messages_for_api = [{"role": "system", "content": current_system_prompt}]
    messages_for_api.extend(conversation_history[chat_id])

    # Store the user's message context for /regenerate BEFORE appending the current user message
    context.user_data['last_user_message_context'] = {
        "chat_id": chat_id,
        "content": processed_user_message,
        "system_prompt_at_time": current_system_prompt,
        "history_at_time": list(conversation_history[chat_id]) # Deep copy current history
    }

    current_user_message_dict = {"role": "user", "content": f"{user_display_name} says: {processed_user_message}"}
    messages_for_api.append(current_user_message_dict)

    user_logger.info(f"Sending to LM Studio (with context and persona): {messages_for_api}")

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        
        response_message = await update.message.reply_text("Thinking...")

        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL_NAME,
            messages=messages_for_api,
            stream=True
        )
        
        lm_studio_response = ""
        
        for chunk in completion:
            if chunk.choices[0].delta.content:
                lm_studio_response += chunk.choices[0].delta.content
                if len(lm_studio_response) % 10 == 0:
                    try:
                        await response_message.edit_text(lm_studio_response + "▌")
                    except Exception as edit_error:
                        user_logger.warning(f"Failed to edit message during streaming: {edit_error}")
                        pass

        if lm_studio_response:
            await response_message.edit_text(lm_studio_response)
        else:
            await response_message.edit_text("Sorry, I didn't get a clear response from the AI.")

        user_logger.info(f"LM Studio response: {lm_studio_response}")

        conversation_history[chat_id].append(current_user_message_dict)
        conversation_history[chat_id].append({"role": "assistant", "content": lm_studio_response})

        if len(conversation_history[chat_id]) > MAX_HISTORY_MESSAGES:
            conversation_history[chat_id] = conversation_history[chat_id][-MAX_HISTORY_MESSAGES:]
            user_logger.info(f"History trimmed for chat_id: {chat_id}. New length: {len(conversation_history[chat_id])}")

    except APITimeoutError as e:
        user_logger.error(f"LM Studio API Timeout Error: {e}", exc_info=True)
        await update.message.reply_text(
            "The AI took too long to respond. This might happen with complex queries or if LM Studio is under heavy load. Please try a simpler query or try again later."
        )
    except APIConnectionError as e:
        user_logger.error(f"LM Studio API Connection Error: {e}", exc_info=True)
        await update.message.reply_text(
            "I couldn't connect to LM Studio. Please ensure LM Studio is running and accessible at the configured address (`LM_STUDIO_API_BASE` in your .env file)."
        )
    except APIStatusError as e:
        user_logger.error(f"LM Studio API Status Error (HTTP {e.status_code}): {e.response.text}", exc_info=True)
        await update.message.reply_text(
            f"LM Studio returned an error (Status {e.status_code}). This might be due to an invalid model name (`LM_STUDIO_MODEL_NAME`) or other API issue. Please check your LM Studio console."
        )
    except Exception as e:
        user_logger.critical(f"An unexpected error occurred during chat_with_lm_studio: {e}", exc_info=True)
        await update.message.reply_text(
            "An unexpected error occurred while processing your request. The bot administrator has been notified. Please try again later."
        )

# --- Regenerate Last Response Feature ---
async def regenerate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"User {user_display_name} initiated /regenerate.")

    last_user_message_data = context.user_data.get('last_user_message_context')

    if not last_user_message_data or last_user_message_data["chat_id"] != chat_id:
        await update.message.reply_text("I don't have a previous message to regenerate for this chat. Please send a message first.")
        user_logger.info(f"Regenerate failed: no last message data for chat_id {chat_id}.")
        return

    # Restore the conversation state from when the last user message was sent
    # This effectively removes the previous AI response (if any) and potential summary
    conversation_history[chat_id] = list(last_user_message_data['history_at_time']) # Restore previous history
    context.user_data['llm_system_prompt'] = last_user_message_data['system_prompt_at_time']
    
    # Log which persona is active during regeneration
    current_persona_name = context.user_data.get('llm_persona_name', 'Default Persona')
    user_logger.info(f"Regenerating response for '{last_user_message_data['content']}' with persona '{current_persona_name}'.")

    processed_user_message = last_user_message_data['content']
    current_system_prompt_for_regeneration = last_user_message_data['system_prompt_at_time'] # Use the prompt that was active then

    # Prepare messages for API call, including the restored history
    messages_for_api = [{"role": "system", "content": current_system_prompt_for_regeneration}]
    messages_for_api.extend(conversation_history[chat_id]) # This is the history *before* the last AI response

    current_user_message_dict = {"role": "user", "content": f"{user_display_name} says: {processed_user_message}"}
    messages_for_api.append(current_user_message_dict) # Add the user's message again

    user_logger.info(f"Sending to LM Studio for regeneration (with restored context): {messages_for_api}")

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        response_message = await update.message.reply_text("Regenerating response...")

        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL_NAME,
            messages=messages_for_api,
            stream=True
        )

        lm_studio_response = ""

        for chunk in completion:
            if chunk.choices[0].delta.content:
                lm_studio_response += chunk.choices[0].delta.content
                if len(lm_studio_response) % 10 == 0:
                    try:
                        await response_message.edit_text(lm_studio_response + "▌")
                    except Exception as edit_error:
                        user_logger.warning(f"Failed to edit message during streaming regeneration: {edit_error}")
                        pass

        if lm_studio_response:
            await response_message.edit_text(lm_studio_response)
        else:
            await response_message.edit_text("Sorry, I didn't get a clear regenerated response from the AI.")

        user_logger.info(f"LM Studio regenerated response: {lm_studio_response}")

        # Update conversation history with the new response
        conversation_history[chat_id].append(current_user_message_dict)
        conversation_history[chat_id].append({"role": "assistant", "content": lm_studio_response})

        # Ensure history is still trimmed after regeneration
        if len(conversation_history[chat_id]) > MAX_HISTORY_MESSAGES:
            conversation_history[chat_id] = conversation_history[chat_id][-MAX_HISTORY_MESSAGES:]
            user_logger.info(f"History trimmed after regeneration for chat_id: {chat_id}. New length: {len(conversation_history[chat_id])}")

    except APITimeoutError as e:
        user_logger.error(f"LM Studio API Timeout Error during regeneration: {e}", exc_info=True)
        await update.message.reply_text("The AI took too long to regenerate. Please try again later.")
    except APIConnectionError as e:
        user_logger.error(f"LM Studio API Connection Error during regeneration: {e}", exc_info=True)
        await update.message.reply_text("I couldn't connect to LM Studio during regeneration. Please ensure it's running.")
    except APIStatusError as e:
        user_logger.error(f"LM Studio API Status Error (HTTP {e.status_code}) during regeneration: {e.response.text}", exc_info=True)
        await update.message.reply_text(f"LM Studio returned an error during regeneration (Status {e.status_code}).")
    except Exception as e:
        user_logger.critical(f"An unexpected error occurred during regenerate_command: {e}", exc_info=True)
        await update.message.reply_text("An unexpected error occurred during regeneration. Please try again later.")


# --- Main Function ---

def main():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
            CommandHandler("persona", set_persona_command),
            CommandHandler("custom_persona", custom_persona_command)
        ],

        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            CHOOSING_PERSONA: [
                CallbackQueryHandler(persona_chosen_callback, per_message=True),
                # If a user sends a text message while in CHOOSING_PERSONA, let them know to use buttons or cancel
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: update.message.reply_text("Please choose a persona using the buttons, or type /cancel.")),
            ],
            ASK_CUSTOM_PERSONA_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_custom_system_prompt)],
            ASK_CUSTOM_SYSTEM_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_custom_persona)],
            CONFIRM_DELETE_PERSONA: [
                CallbackQueryHandler(handle_delete_persona_callback, per_message=True),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: update.message.reply_text("Please use the buttons to delete a persona, or type /cancel to go back."))
            ],
        },

        fallbacks=[CommandHandler("cancel", cancel_name_collection)],
        allow_reentry=True
    )
    application.add_handler(conv_handler)

    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("regenerate", regenerate_command)) # Add the new command handler

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND &
        (filters.ChatType.PRIVATE | filters.ChatType.GROUPS | filters.REPLY),
        chat_with_lm_studio
    ))

    logger.info("Bot started! Listening for messages...")
    if DEBUG_MODE:
        logger.debug("DEBUG_MODE is enabled. More verbose logging will be printed to console.")

    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"Bot stopped due to an unhandled error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()