import logging
from telegram import Update, MessageEntity
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import sys

# Ensure you have installed 'openai' for LM Studio API and 'python-telegram-bot'
# You can install them using:
# pip install python-telegram-bot openai
from openai import OpenAI

# Configure logging to show information about bot activity and errors
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
TELEGRAM_BOT_TOKEN = "7838169211:AAHKReBSAS17PTAfx8s6BIFfp0MRmUK57c4"
LM_STUDIO_API_BASE = "http://192.168.100.1:4443/v1"
LM_STUDIO_MODEL_NAME = "mistral-small-22b-arliai-rpmax-v1.1"

# Initialize the OpenAI client
lm_studio_client = OpenAI(
    base_url=LM_STUDIO_API_BASE,
    api_key="lm-studio"
)

# --- Conversational Memory Storage ---
conversation_history = {}
MAX_HISTORY_MESSAGES = 10

# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sends a welcome message and clears conversation history for the user.
    Also lists available commands.
    """
    chat_id = update.effective_chat.id
    if chat_id in conversation_history:
        del conversation_history[chat_id] # Clear history on /start
        logger.info(f"Conversation history cleared for chat_id: {chat_id}")

    welcome_message = (
        'Hi! I am Olver, your horny AI Lion bot. '
        'In private chat, I respond to all messages. '
        'In groups, please mention me (@Oliver_handsome_bot) or reply to my messages.'
        '\n\n'
        'Here are my commands:\n'
        '/start - Start a new conversation (also clears history)\n'
        '/clear - Clear our conversation history'
    )

    if update.message.chat.type == "private" or \
       (update.message.text and f"@{context.bot.username}" in update.message.text):
        await update.message.reply_text(welcome_message)
    else:
        pass

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Clears the conversation history for the current user.
    """
    chat_id = update.effective_chat.id
    if chat_id in conversation_history:
        del conversation_history[chat_id]
        logger.info(f"Conversation history manually cleared for chat_id: {chat_id}")
        await update.message.reply_text("Our conversation history has been cleared!")
    else:
        await update.message.reply_text("There's no conversation history to clear for this chat.")


async def chat_with_lm_studio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Processes user messages, manages conversation history, and sends to LM Studio.
    """
    original_user_message = update.message.text
    bot_username = context.bot.username
    chat_id = update.effective_chat.id # Get the current chat ID

    # --- Retrieve User Identifier ---
    user_identifier = update.effective_user.username
    if user_identifier:
        user_identifier = f"@{user_identifier}"
    else:
        user_identifier = update.effective_user.first_name or "a user"
    logger.info(f"Interaction from {user_identifier}.")


    # --- Filtering Logic for Group Chats ---
    processed_user_message = original_user_message # Initialize

    if update.message.chat.type in ["group", "supergroup"]:
        if update.message.reply_to_message and \
           update.message.reply_to_message.from_user and \
           update.message.reply_to_message.from_user.id == context.bot.id:
            logger.info(f"Received reply to bot from {user_identifier} in group: {original_user_message}")
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
                logger.info(f"Ignoring non-addressed message in group from {user_identifier}: {original_user_message}")
                return
        else:
            logger.info(f"Ignoring non-addressed message in group from {user_identifier}: {original_user_message}")
            return
    elif update.message.chat.type == "private":
        logger.info(f"Received private message from {user_identifier}: {original_user_message}")
        processed_user_message = original_user_message
    else:
        logger.info(f"Ignoring message from unsupported chat type '{update.message.chat.type}': {original_user_message}")
        return

    if not processed_user_message:
        logger.info(f"User message is empty after processing, asking for help.")
        await update.message.reply_text("Hi there! How can I help you?")
        return

    # --- Prepare Message for LLM with Context ---
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []

    current_user_message_dict = {"role": "user", "content": f"User {user_identifier} says: {processed_user_message}"}

    messages_for_api = []
    messages_for_api.extend(conversation_history[chat_id])
    messages_for_api.append(current_user_message_dict)

    logger.info(f"Sending to LM Studio (with context): {messages_for_api}")

    try:
        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL_NAME,
            messages=messages_for_api,
        )
        lm_studio_response = completion.choices[0].message.content
        logger.info(f"LM Studio response: {lm_studio_response}")

        # --- Update Conversation History ---
        conversation_history[chat_id].append(current_user_message_dict)
        conversation_history[chat_id].append({"role": "assistant", "content": lm_studio_response})

        if len(conversation_history[chat_id]) > MAX_HISTORY_MESSAGES:
            conversation_history[chat_id] = conversation_history[chat_id][-MAX_HISTORY_MESSAGES:]
            logger.info(f"History trimmed for chat_id: {chat_id}. New length: {len(conversation_history[chat_id])}")

        await update.message.reply_text(lm_studio_response)

    except Exception as e:
        logger.error(f"Error communicating with LM Studio: {e}", exc_info=True)
        await update.message.reply_text(
            "Sorry, I'm having trouble connecting to the AI or getting a response. "
            "Please try again later, and check your LM Studio console for errors."
            "It can happen that i'm offline for now. Try it again later"
        )

# --- Main Function ---

def main():
    """Starts the bot and handles unhandled exceptions during polling."""
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
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