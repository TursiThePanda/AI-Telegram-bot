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
# Replace with your actual Telegram Bot Token from BotFather
TELEGRAM_BOT_TOKEN = "7838169211:AAHKReBSAS17PTAfx8s6BIFfp0MRmUK57c4"
# Replace with the actual IP address and port where your LM Studio API server is running
# Make sure LM Studio is running and its server is active.
LM_STUDIO_API_BASE = "http://192.168.100.1:4443/v1"
# IMPORTANT: This must EXACTLY match the identifier of the model loaded in LM Studio.
# You can find this in LM Studio's "Local Server" tab under the "Model" dropdown.
LM_STUDIO_MODEL_NAME = "mistral-small-22b-arliai-rpmax-v1.1"

# Initialize the OpenAI client to communicate with your local LM Studio server
lm_studio_client = OpenAI(
    base_url=LM_STUDIO_API_BASE,
    api_key="lm-studio" # For LM Studio's local server, this can be any string.
)

# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sends a welcome message when the /start command is issued.
    Responds in private chats or if explicitly mentioned in a group.
    """
    if update.message.chat.type == "private" or \
       (update.message.text and f"@{context.bot.username}" in update.message.text):
        await update.message.reply_text(
            'Hi! I am your LM Studio AI bot. '
            'In private chat, I respond to all messages. '
            'In groups, please mention me (@YourBotName) or reply to my messages.'
        )
    else:
        # Ignore /start if it's in a group and not directly addressed
        pass

async def chat_with_lm_studio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Processes user messages and sends them to LM Studio for a response.
    Includes logic to handle messages only when addressed to the bot in groups.
    """
    original_user_message = update.message.text
    bot_username = context.bot.username

    # --- Retrieve User Identifier ---
    # Prioritize username, fallback to first_name, then generic "user"
    user_identifier = update.effective_user.username
    if user_identifier:
        user_identifier = f"@{user_identifier}" # Add @ for clarity if it's a username
    else:
        user_identifier = update.effective_user.first_name or "a user"
    logger.info(f"Interaction from {user_identifier}.")


    # --- Filtering Logic for Group Chats ---
    # In group chats, we only want to respond if the bot is mentioned or replied to.
    if update.message.chat.type in ["group", "supergroup"]:
        # Case 1: The message is a direct reply to the bot's message
        if update.message.reply_to_message and \
           update.message.reply_to_message.from_user and \
           update.message.reply_to_message.from_user.id == context.bot.id:
            logger.info(f"Received reply to bot from {user_identifier} in group: {original_user_message}")
            # The message text is already clean for LM Studio, no need to strip mention.
            processed_user_message = original_user_message

        # Case 2: The message explicitly mentions the bot's username
        elif update.message.entities:
            is_addressed_to_bot = False
            for entity in update.message.entities:
                # Check if the entity is a mention and if it matches this bot's username
                if entity.type == MessageEntity.MENTION and \
                   original_user_message[entity.offset : entity.offset + entity.length] == f"@{bot_username}":
                    is_addressed_to_bot = True
                    # Remove the bot's mention from the message before sending to LM Studio
                    processed_user_message = original_user_message.replace(f"@{bot_username}", "").strip()
                    break # Stop checking entities once our bot is mentioned
            if not is_addressed_to_bot:
                logger.info(f"Ignoring non-addressed message in group from {user_identifier}: {original_user_message}")
                return # If not addressed to the bot, exit the function
        else:
            # If it's a group chat and neither a reply nor a mention, ignore the message.
            logger.info(f"Ignoring non-addressed message in group from {user_identifier}: {original_user_message}")
            return
    # In private chats, always respond to all messages
    elif update.message.chat.type == "private":
        logger.info(f"Received private message from {user_identifier}: {original_user_message}")
        processed_user_message = original_user_message
    # For any other chat types (like channels), log and ignore for now
    else:
        logger.info(f"Ignoring message from unsupported chat type '{update.message.chat.type}': {original_user_message}")
        return

    # After filtering, if the user_message is empty (e.g., they only sent "@BotName"),
    # send a polite prompt or ignore it.
    if not processed_user_message:
        logger.info(f"User message is empty after processing, asking for help.")
        await update.message.reply_text("Hi there! How can I help you?")
        return

    # --- Inject User Identifier into the Message for LLM ---
    # This prepends the user's identifier to their actual message.
    # The LLM will now see "User @username says: [original message]"
    message_for_llm = f"User {user_identifier} says: {processed_user_message}"
    logger.info(f"Sending to LM Studio: {message_for_llm}")

    try:
        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL_NAME,
            messages=[
                # System message is set in LM Studio's UI
                {"role": "user", "content": message_for_llm} # Sending the augmented message
            ],
        )
        lm_studio_response = completion.choices[0].message.content
        logger.info(f"LM Studio response: {lm_studio_response}")

        await update.message.reply_text(lm_studio_response)

    except Exception as e:
        logger.error(f"Error communicating with LM Studio: {e}", exc_info=True)
        await update.message.reply_text(
            "Sorry, I'm having trouble connecting to the AI or getting a response. "
            "Please try again later, and check your LM Studio console for errors."
        )

# --- Main Function ---

def main():
    """Starts the bot and handles unhandled exceptions during polling."""
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
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