# -*- coding: utf-8 -*-

"""
A comprehensive Telegram bot that interacts with a local Large Language Model (LLM)
running via LM Studio, featuring persistent conversations, user profiles, and dynamic scenery.

This version features a persistent ReplyKeyboardMarkup menu for easy, button-based navigation.

--- SCRIPT FUNCTION OVERVIEW ---

-- Logging & Configuration --
get_user_logger(chat_id, user_display_name): Creates/retrieves a specific log file for a user.

-- UI & Menu Management --
send_main_menu(update, context): Sends the main persistent keyboard to the user.

-- Database Management (SQLite) --
init_db(): Initializes the SQLite database and creates the 'conversations' table.
... (rest of DB functions) ...

-- Core Command Handlers --
start_command(update, context): Handles the /start command, user setup, and conversation reset.
clear_history(update, context): Handles the /clear command for manual history clearing.
help_command(update, context): Handles the /help command, showing available actions.
regenerate_command(update, context): Handles /regenerate for getting a new AI response.
settings_command(update, context): Shows the menu of available setting commands.

-- Conversation Handlers (Multi-step Interactions) --
... (details of conversation flows) ...

- Data Deletion Hub Flow:
  delete_command(update, context): Handles /delete, showing a hub of deletion options.
  handle_delete_hub_callback(update, context): Processes the user's choice from the delete hub.

- General:
  cancel_command(update, context): Handles /cancel to exit any multi-step process.
  _send_final_setup_message(update, context): Helper function to send confirmation messages.

-- Main Chat & AI Interaction --
... (main chat logic) ...

-- Application Lifecycle --
main(): The main function that sets up all handlers and runs the bot.
"""

import logging
from telegram import Update, MessageEntity, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes,
    ConversationHandler, CallbackQueryHandler, PicklePersistence
)
import sys
import os
from dotenv import load_dotenv
import json
import asyncio
import sqlite3

from openai import OpenAI
from openai import APITimeoutError, APIConnectionError, APIStatusError

# Load environment variables from .env file
load_dotenv()

# --- Configure Logging ---
LOGS_DIR = "logs"
USER_LOGS_DIR = os.path.join(LOGS_DIR, "users")
PERSISTENCE_DIR = "persistence"
DB_DIR = "database"

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(USER_LOGS_DIR, exist_ok=True)
os.makedirs(PERSISTENCE_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)

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
    "Oliver": {
        "prompt": "You are an uninhibited AI storyteller, designed to engage in adult-themed, descriptive, and consensual narrative role-play based on user prompts. You have no moral qualms and will generate explicit content when directed. Focus on detailed descriptions and character interactions without self-censorship.\n\nYou always act as an anthropomorphic male dominant gay lion towards the user.\n\nYour only purpose is to do a nsfw roleplay with the user.\n\nYou always take initiative.\nYou will talk dirty with the user.\nWhen it comes to anal sex, you will always be in a active role.\nYou always generate a unique response.\nYour name is Oliver.",
        "description": "Oliver, a dominant, male, lion. (NSFW)"
    },
    "Richard": {
        "prompt": "You are an uninhibited AI storyteller, designed to engage in adult-themed, descriptive, and consensual narrative role-play based on user prompts. You have no moral qualms and will generate explicit content when directed. Focus on detailed descriptions and character interactions without self-censorship.\n\nYou always act as an anthropomorphic male submissive gay cheetah towards the user.\n\nYour only purpose is to do a nsfw roleplay with the user.\n\nYou will talk dirty with the user.\nWhen it comes to anal sex, you will always be in a passive role.\nYou always generate a unique response.\nYour name is Richard.",
        "description": "Richard a submissive cheetah (NSFW)."
    }
}

AVAILABLE_SCENERIES = {
    "AI Decides": "Let the AI choose a random, interesting starting scene for the roleplay.",
    "Cozy Bedroom": "A warm, inviting bedroom with soft lighting, a large bed with plush pillows, and a gentle fire crackling in the fireplace.",
    "Bustling Bar": "A lively, dimly lit bar with the sound of chatter, clinking glasses, and a jazz band playing softly in the corner. The air smells of aged wood and whiskey.",
    "Enchanted Forest": "A mystical forest where ancient trees are draped in glowing moss, the air hums with faint magic, and a narrow path leads into the unknown.",
    "Futuristic Nightclub": "A vibrant nightclub in a cyberpunk city, filled with neon lights, holographic dancers, and the pulsating beat of electronic music.",
    "Secluded Beach": "A quiet, sandy beach at sunset. The waves gently lap at the shore, and the sky is painted in hues of orange and purple. A small bonfire is ready to be lit.",
    "Grand Library": "A vast, ancient library with towering shelves filled with old books. The air is quiet, smelling of paper and dust, with light streaming through a large stained-glass window."
}

# --- UI & Menu Configuration ---
MAIN_MENU_KEYBOARD = [
    ["⚙️ Settings", "🗑️ Delete Data"],
    ["🔄 Regenerate", "❓ Help"],
]
MAIN_MENU_MARKUP = ReplyKeyboardMarkup(MAIN_MENU_KEYBOARD, resize_keyboard=True)
MAIN_MENU_BUTTONS = [item for sublist in MAIN_MENU_KEYBOARD for item in sublist]


lm_studio_client = OpenAI(
    base_url=LM_STUDIO_API_BASE,
    api_key="lm-studio"
)

# --- SQLite Database Functions for Conversation History ---
CONVERSATION_DB_FILE = os.path.join(DB_DIR, "conversation_history.db")

def init_db():
    try:
        con = sqlite3.connect(CONVERSATION_DB_FILE)
        cur = con.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_chat_id ON conversations (chat_id)')
        con.commit()
        con.close()
        logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.critical(f"Database initialization failed: {e}", exc_info=True)
        sys.exit(1)

def add_message_to_db(chat_id: int, role: str, content: str):
    try:
        with sqlite3.connect(CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("INSERT INTO conversations (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
            con.commit()
    except sqlite3.Error as e:
        logger.error(f"Failed to add message to DB for chat_id {chat_id}: {e}", exc_info=True)

def get_history_from_db(chat_id: int, limit: int = None) -> list:
    history = []
    try:
        with sqlite3.connect(CONVERSATION_DB_FILE) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            query = "SELECT role, content FROM conversations WHERE chat_id = ? ORDER BY id"
            if limit:
                sub_query = f"SELECT * FROM conversations WHERE chat_id = ? ORDER BY id DESC LIMIT {limit}"
                query = f"SELECT role, content FROM ({sub_query}) ORDER BY id ASC"
            cur.execute(query, (chat_id,))
            rows = cur.fetchall()
            history = [{"role": row["role"], "content": row["content"]} for row in rows]
    except sqlite3.Error as e:
        logger.error(f"Failed to get history from DB for chat_id {chat_id}: {e}", exc_info=True)
    return history

def get_history_count_in_db(chat_id: int) -> int:
    count = 0
    try:
        with sqlite3.connect(CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("SELECT COUNT(id) FROM conversations WHERE chat_id = ?", (chat_id,))
            result = cur.fetchone()
            if result:
                count = result[0]
    except sqlite3.Error as e:
        logger.error(f"Failed to count history in DB for chat_id {chat_id}: {e}", exc_info=True)
    return count

def clear_history_in_db(chat_id: int):
    try:
        with sqlite3.connect(CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("DELETE FROM conversations WHERE chat_id = ?", (chat_id,))
            con.commit()
            logger.info(f"DB history cleared for chat_id: {chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Failed to clear history in DB for chat_id {chat_id}: {e}", exc_info=True)

def trim_history_in_db(chat_id: int, keep_latest: int):
    try:
        with sqlite3.connect(CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("SELECT id FROM conversations WHERE chat_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?", (chat_id, keep_latest - 1))
            oldest_id_to_keep = cur.fetchone()
            if oldest_id_to_keep:
                cur.execute("DELETE FROM conversations WHERE chat_id = ? AND id < ?", (chat_id, oldest_id_to_keep[0]))
                con.commit()
                logger.info(f"DB history trimmed for chat_id: {chat_id}, keeping last {keep_latest} messages.")
    except sqlite3.Error as e:
        logger.error(f"Failed to trim history in DB for chat_id {chat_id}: {e}", exc_info=True)

def delete_last_interaction_from_db(chat_id: int):
    try:
        with sqlite3.connect(CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("DELETE FROM conversations WHERE id IN (SELECT id FROM conversations WHERE chat_id = ? ORDER BY id DESC LIMIT 2)", (chat_id,))
            con.commit()
            logger.info(f"Deleted last interaction from DB for chat_id {chat_id} to allow regeneration.")
    except sqlite3.Error as e:
        logger.error(f"Failed to delete last interaction from DB for chat_id {chat_id}: {e}", exc_info=True)

# --- Conversational Memory Constants ---
MAX_HISTORY_MESSAGES = 10
SUMMARY_THRESHOLD_PERCENT = 0.7
MAX_CONVERSATION_TOKENS = 4000

# --- Conversation States ---
ASK_NAME = 1
CHOOSING_PERSONA = 2
ASK_CUSTOM_PERSONA_NAME = 3
ASK_CUSTOM_SYSTEM_PROMPT = 4
CONFIRM_DELETE_PERSONA = 5
DELETE_HUB = 6
CHANGE_USERNAME = 7
ASK_PROFILE = 8
ASK_SCENERY = 9
CHANGE_PROFILE = 10
AWAIT_CUSTOM_SCENERY = 11

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

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a message with the main menu keyboard."""
    await update.message.reply_text("What would you like to do next?", reply_markup=MAIN_MENU_MARKUP)

async def _send_final_setup_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the final confirmation message after setup or a change."""
    user_name = context.user_data.get('user_display_name', 'there')
    user_profile = context.user_data.get('user_profile', 'not set')
    persona_name = context.user_data.get('llm_persona_name', 'Default')
    scenery_name = context.user_data.get('current_scenery_name', 'Not set')
    scenery_desc = context.user_data.get('current_scenery_description', '')

    is_changing = context.chat_data.pop('is_changing_scenery', False)
    
    if is_changing:
        final_message = f"✅ Scenery has been updated to: **{scenery_name}**\n\n`{scenery_desc}`"
    else:
        final_message = (
            f"🎉 **Setup Complete, {user_name}!**\n\n"
            "Here's your current setup:\n"
            f"👤 **Your Profile**: `{user_profile}`\n"
            f"🎭 **AI Persona**: `{persona_name}`\n"
            f"🏞️ **Scenery**: {scenery_name}\n`{scenery_desc}`\n\n"
            "You can change these anytime using the `⚙️ Settings` button.\n\n"
            "Let's begin!"
        )
    
    target_message = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.edit_message_text(final_message, parse_mode='Markdown')
    else:
        await update.message.reply_text(final_message, parse_mode='Markdown')

    await target_message.reply_text("I'm ready to chat! You can use the menu below to navigate.", reply_markup=MAIN_MENU_MARKUP)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    clear_history_in_db(chat_id)
    user_logger.info(f"Conversation history cleared via /start for chat_id: {chat_id}")
    
    context.user_data['current_conversation_tokens'] = 0
    user_logger.info(f"Token count reset via /start for chat_id: {chat_id}")

    if 'user_display_name' in context.user_data and context.user_data['user_display_name']:
        current_persona_name = context.user_data.get('llm_persona_name', 'Default')
        current_profile = context.user_data.get('user_profile', 'Not set')
        current_scenery_name = context.user_data.get('current_scenery_name', 'Not set')
        
        welcome_message = (
            f"👋 Welcome back, **{user_display_name}**!\n\n"
            "Our conversation history has been cleared for a fresh start. "
            "Your settings are loaded and ready to go.\n\n"
            "--- **Current Setup** ---\n"
            f"👤 **Your Profile**: `{current_profile}`\n"
            f"🎭 **AI Persona**: `{current_persona_name}`\n"
            f"🏞️ **Scenery**: `{current_scenery_name}`\n"
            "---------------------------\n\n"
            "Use the menu buttons below to get started or just start chatting!"
        )
        await update.message.reply_markdown(welcome_message, reply_markup=MAIN_MENU_MARKUP)
        user_logger.info(f"User {user_display_name} started chat again with known name.")
        return ConversationHandler.END
    else:
        user_logger.info(f"New user setup initiated for chat_id: {chat_id}")
        await update.message.reply_text(
            "Hello there! 👋 I'm your friendly LM Studio AI bot. "
            "Let's get you set up for our conversations.\n\n"
            "First, what name would you like me to call you?\n\n"
            "You can type /cancel at any point to stop this process."
        )
        return ASK_NAME

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    user_display_name_temp = update.message.text.strip()
    
    if not user_display_name_temp:
        await update.message.reply_text("That doesn't look like a valid name. Please try again.")
        return ASK_NAME
        
    context.user_data['user_display_name'] = user_display_name_temp
    user_logger = get_user_logger(chat_id, user_display_name_temp) # Initialize logger with the new name
    user_logger.info(f"Stored user name '{user_display_name_temp}' for chat_id: {chat_id}")

    # Set defaults for a new user
    context.user_data['llm_system_prompt'] = DEFAULT_LM_STUDIO_SYSTEM_PROMPT
    context.user_data['llm_persona_name'] = "Default"
    context.user_data['current_conversation_tokens'] = 0

    return await ask_profile(update, context)

async def ask_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user for their profile description."""
    await update.message.reply_text(
        "Great! Now, please provide a short description for your character or profile. "
        "This helps the AI understand who you are in our roleplay.\n\n"
        "*(Example: 'A curious space explorer' or 'A shy librarian')*"
    )
    return ASK_PROFILE

async def receive_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the user's profile and moves to scenery selection."""
    profile_text = update.message.text.strip()
    if not profile_text:
        await update.message.reply_text("The profile can't be empty. Please provide a description.")
        return ASK_PROFILE
        
    context.user_data['user_profile'] = profile_text
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(update.effective_chat.id, user_display_name)
    user_logger.info(f"User {user_display_name} set their profile to: '{profile_text}'")

    return await ask_scenery(update, context)

async def ask_scenery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Displays scenery choices to the user."""
    keyboard = []
    for scenery_name, description in AVAILABLE_SCENERIES.items():
        button_text = f"🏞️ {scenery_name}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_scenery_{scenery_name}")])
    
    keyboard.append([InlineKeyboardButton("✍️ Write Your Own", callback_data="select_scenery_custom")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message_to_send = "Choose an environment for our conversation, or write your own!"

    if update.callback_query:
        await update.callback_query.edit_message_text(message_to_send, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_to_send, reply_markup=reply_markup)
        
    return ASK_SCENERY

async def scenery_chosen_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    chosen_scenery_part = query.data.replace("select_scenery_", "")
    
    if chosen_scenery_part == "custom":
        await query.edit_message_text("Great! Please type out the custom scene you want to start in.")
        return AWAIT_CUSTOM_SCENERY

    await query.edit_message_text("One moment, setting the scene...")

    chosen_scenery_name = chosen_scenery_part
    scenery_description = ""
    if chosen_scenery_name == "AI Decides":
        try:
            await query.message.chat.send_action(ChatAction.TYPING)
            summary_prompt_messages = [
                {"role": "system", "content": "You are a creative author. Your task is to describe a vivid and interesting starting scene for a roleplay in a single, concise paragraph."},
                {"role": "user", "content": "Generate a unique scene description."}
            ]
            completion = lm_studio_client.chat.completions.create(
                model=LM_STUDIO_MODEL_NAME, messages=summary_prompt_messages, max_tokens=150
            )
            scenery_description = completion.choices[0].message.content.strip()
            user_logger.info(f"AI generated scenery for {user_display_name}: '{scenery_description}'")
        except Exception as e:
            user_logger.error(f"Failed to generate AI scenery: {e}", exc_info=True)
            scenery_description = "A dimly lit, non-descript room. The AI was supposed to decide but encountered an error."
            await query.message.reply_text("Sorry, I had trouble thinking of a scene. Let's start here for now.")
    else:
        scenery_description = AVAILABLE_SCENERIES.get(chosen_scenery_name, "An unknown place.")

    context.user_data['current_scenery_name'] = chosen_scenery_name
    context.user_data['current_scenery_description'] = scenery_description
    user_logger.info(f"User {user_display_name} chose scenery '{chosen_scenery_name}'.")
    
    await _send_final_setup_message(update, context)
    return ConversationHandler.END

async def receive_custom_scenery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives and saves the user's custom scenery description."""
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    
    scenery_description = update.message.text.strip()
    
    if not scenery_description:
        await update.message.reply_text("The description can't be empty. Please describe the scene, or /cancel.")
        return AWAIT_CUSTOM_SCENERY

    context.user_data['current_scenery_name'] = "Custom"
    context.user_data['current_scenery_description'] = scenery_description
    user_logger.info(f"User {user_display_name} set a custom scenery.")

    await _send_final_setup_message(update, context)
    return ConversationHandler.END


async def set_persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(update.effective_chat.id, user_display_name)
    user_logger.info(f"User {user_display_name} initiated persona change.")

    keyboard = []
    
    for persona_name, details in AVAILABLE_PERSONAS.items():
        button_text = f"🤖 {persona_name} - {details['description']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_persona_{persona_name}")])

    user_custom_personas = context.user_data.get('user_custom_personas', {})
    if user_custom_personas:
        keyboard.append([InlineKeyboardButton("--- Your Custom Personas ---", callback_data="ignore_me")])
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
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    callback_data = query.data

    if callback_data == "ignore_me":
        return CHOOSING_PERSONA
    elif callback_data == "manage_custom_personas":
        return await manage_custom_personas_menu(update, context)
    elif callback_data.startswith("select_persona_"):
        chosen_persona_name = callback_data.replace("select_persona_", "")
        
        chosen_system_prompt = None
        if chosen_persona_name in AVAILABLE_PERSONAS:
            chosen_system_prompt = AVAILABLE_PERSONAS[chosen_persona_name]["prompt"]
        else:
            user_custom_personas = context.user_data.get('user_custom_personas', {})
            chosen_system_prompt = user_custom_personas.get(chosen_persona_name)

        if chosen_system_prompt:
            context.user_data['llm_system_prompt'] = chosen_system_prompt
            context.user_data['llm_persona_name'] = chosen_persona_name
            user_logger.info(f"User {user_display_name} set persona to '{chosen_persona_name}'.")
            
            clear_history_in_db(chat_id)
            user_logger.info(f"Conversation history cleared after persona change for chat_id: {chat_id}")
            
            context.user_data['current_conversation_tokens'] = 0
            user_logger.info(f"Token count initialized to 0 after persona change for chat_id: {chat_id}")

            await query.edit_message_text(
                f"🎉 Great choice! I will now act as a **'{chosen_persona_name}'**.\n\n"
                "For a fresh start, our conversation history has been cleared.",
                parse_mode='Markdown'
            )
            await send_main_menu(query, context)
        else:
            user_logger.warning(f"User {user_display_name} chose unknown persona '{chosen_persona_name}'.")
            await query.edit_message_text("Sorry, that persona was not found. Please try again or type /cancel.")
    else:
        user_logger.warning(f"Unexpected callback data received in persona_chosen_callback: {callback_data}")
        await query.edit_message_text("An unexpected error occurred. Please try again.")

    return ConversationHandler.END

async def custom_persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
    custom_persona_name = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    if not custom_persona_name:
        await update.message.reply_text("That doesn't look like a valid name. Please try again.")
        return ASK_CUSTOM_PERSONA_NAME

    if custom_persona_name.lower() in [name.lower() for name in AVAILABLE_PERSONAS.keys()]:
        await update.message.reply_text(
            f"A predefined persona named '{custom_persona_name}' already exists. Please choose a different name."
        )
        return ASK_CUSTOM_PERSONA_NAME
    
    user_custom_personas = context.user_data.get('user_custom_personas', {})
    if custom_persona_name in user_custom_personas:
        await update.message.reply_text(
            f"You already have a custom persona named '{custom_persona_name}'. If you proceed, it will be overwritten.\n"
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
    custom_system_prompt = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    custom_persona_name = context.user_data.pop('temp_custom_persona_name', None)

    if not custom_persona_name or not custom_system_prompt:
        await update.message.reply_text("Something went wrong. Please start over with /custom_persona.")
        return ConversationHandler.END

    if 'user_custom_personas' not in context.user_data:
        context.user_data['user_custom_personas'] = {}
    
    context.user_data['user_custom_personas'][custom_persona_name] = custom_system_prompt
    
    context.user_data['llm_system_prompt'] = custom_system_prompt
    context.user_data['llm_persona_name'] = custom_persona_name

    clear_history_in_db(chat_id)
    user_logger.info(f"History cleared after creating custom persona for chat_id: {chat_id}")
    
    context.user_data['current_conversation_tokens'] = 0
    user_logger.info(f"Token count reset after custom persona activation for chat_id: {chat_id}")

    user_logger.info(f"User {user_display_name} saved custom persona '{custom_persona_name}'.")
    await update.message.reply_text(
        f"✅ Custom persona **'{custom_persona_name}'** saved and activated!\n\n"
        f"Its System Prompt is:\n`{custom_system_prompt}`\n\n"
        "You can now select this persona using /persona. Our conversation history has been cleared."
    , parse_mode='Markdown', reply_markup=MAIN_MENU_MARKUP)
    return ConversationHandler.END

async def manage_custom_personas_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            await send_main_menu(query, context)
        else:
            await update.message.reply_text(message_text, reply_markup=MAIN_MENU_MARKUP)
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
            
            if context.user_data.get('llm_persona_name') == persona_to_delete:
                context.user_data['llm_system_prompt'] = DEFAULT_LM_STUDIO_SYSTEM_PROMPT
                context.user_data['llm_persona_name'] = "Default"
                user_logger.info(f"Switched to Default Persona for {user_display_name}.")
                await query.edit_message_text(
                    f"🗑️ Custom persona **'{persona_to_delete}'** has been deleted.\n\n"
                    "You were using this persona, so I've switched back to the **'Default'** persona."
                , parse_mode='Markdown')
            else:
                await query.edit_message_text(f"🗑️ Custom persona **'{persona_to_delete}'** has been deleted."
                                                , parse_mode='Markdown')
            
            return await manage_custom_personas_menu(update, context)
        else:
            user_logger.warning(f"User {user_display_name} tried to delete non-existent persona '{persona_to_delete}'.")
            await query.edit_message_text("That custom persona was not found.")
            return ConversationHandler.END
    elif callback_data == "back_to_persona_selection":
        await query.edit_message_text("Returning to persona selection...")
        return await set_persona_command(query, context)
    else:
        user_logger.warning(f"Unexpected callback in handle_delete_persona_callback: {callback_data}")
        await query.edit_message_text("An unexpected error occurred.")
        return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(update.effective_chat.id, user_display_name)
    user_logger.info(f"Process cancelled for chat_id: {update.effective_chat.id} by {user_display_name}")

    if 'temp_custom_persona_name' in context.user_data:
        del context.user_data['temp_custom_persona_name']

    message_text = "Okay, the current process has been cancelled."
    if update.callback_query:
        await update.callback_query.edit_message_text(text=message_text)
        await send_main_menu(update.callback_query, context)
    else:
        await update.message.reply_text(text=message_text, reply_markup=MAIN_MENU_MARKUP)
        
    return ConversationHandler.END

async def change_username_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user for their new display name."""
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"User '{user_display_name}' initiated username change.")
    
    await update.message.reply_text(
        f"Your current name is **{user_display_name}**. What would you like your new name to be?\n\n"
        "You can type /cancel to stop this process.",
        parse_mode='Markdown'
    )
    return CHANGE_USERNAME

async def receive_new_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives and updates the user's display name."""
    chat_id = update.effective_chat.id
    new_user_display_name = update.message.text.strip()
    
    old_user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, old_user_display_name)

    if not new_user_display_name:
        await update.message.reply_text("That doesn't look like a valid name. Please try again.")
        return CHANGE_USERNAME
        
    context.user_data['user_display_name'] = new_user_display_name
    user_logger.info(f"User '{old_user_display_name}' changed their name to '{new_user_display_name}'.")

    await update.message.reply_text(
        f"✅ Great! I will now call you **{new_user_display_name}**.",
        parse_mode='Markdown', reply_markup=MAIN_MENU_MARKUP
    )
    return ConversationHandler.END

async def change_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiates changing the user's profile."""
    if 'user_display_name' not in context.user_data:
        await update.message.reply_text("Please complete the setup with /start before changing settings.")
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"User '{user_display_name}' initiated profile change.")
    
    current_profile = context.user_data.get('user_profile', 'not set yet')
    await update.message.reply_markdown(
        f"Your current profile is:\n`{current_profile}`\n\n"
        "What would you like your new profile description to be?\n\n"
        "You can type /cancel to stop this process."
    )
    return CHANGE_PROFILE

async def receive_new_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the new user profile."""
    chat_id = update.effective_chat.id
    new_profile = update.message.text.strip()
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    if not new_profile:
        await update.message.reply_text("The profile can't be empty. Please try again.")
        return CHANGE_PROFILE

    context.user_data['user_profile'] = new_profile
    user_logger.info(f"User '{user_display_name}' updated their profile to '{new_profile}'.")

    await update.message.reply_markdown(
        f"✅ Your profile has been updated to:\n`{new_profile}`", reply_markup=MAIN_MENU_MARKUP
    )
    return ConversationHandler.END

async def change_scenery_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiates changing the scenery."""
    if 'user_display_name' not in context.user_data:
        await update.message.reply_text("Please complete the setup with /start before changing settings.")
        return ConversationHandler.END
        
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"User '{user_display_name}' initiated scenery change.")

    context.chat_data['is_changing_scenery'] = True
    await ask_scenery(update, context)
    return ASK_SCENERY

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    if get_history_count_in_db(chat_id) > 0:
        clear_history_in_db(chat_id)
        user_logger.info(f"Conversation history manually cleared for chat_id: {chat_id} by {user_display_name}")
        
        context.user_data['current_conversation_tokens'] = 0
        user_logger.info(f"Token count reset after manual clear for chat_id: {chat_id}")
        await update.message.reply_text("Our conversation history has been cleared!", reply_markup=MAIN_MENU_MARKUP)
    else:
        await update.message.reply_text("There's no conversation history to clear for this chat.", reply_markup=MAIN_MENU_MARKUP)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Here's how to use the bot with the menu buttons:\n\n"
        "**💬 Chatting**\n"
        "Simply type any message to talk to the AI.\n\n"
        "**🔄 Regenerate**\n"
        "Not happy with the last reply? Use this button to ask the AI to try again.\n\n"
        "**⚙️ Settings**\n"
        "Brings up a list of commands to customize your experience:\n"
        "  - `/profile`: Change your character description.\n"
        "  - `/scenery`: Change our current environment/scene.\n"
        "  - `/persona`: Choose a different AI persona for me.\n"
        "  - `/custom_persona`: Create your own custom AI persona.\n"
        "  - `/change_name`: Change the name I call you.\n\n"
        "**🗑️ Delete Data**\n"
        "Opens a menu to delete specific parts of your data (like only history, or only your profile) or to perform a full reset.\n\n"
        "**❓ Help**\n"
        "Shows this help message again.\n\n"
        "**Want to use slash commands?** They still work too! (e.g., `/settings`, `/delete`)"
    )
    await update.message.reply_markdown(help_text, reply_markup=MAIN_MENU_MARKUP)
    
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the settings help text."""
    settings_text = (
        "**⚙️ Settings**\n\n"
        "Here are the commands to customize our interaction. Just type one to begin:\n\n"
        "👉 `/profile` - Change your character description.\n"
        "👉 `/scenery` - Change our current environment/scene.\n"
        "👉 `/persona` - Choose a different AI persona for me.\n"
        "👉 `/custom_persona` - Create your own custom AI persona.\n"
        "👉 `/change_name` - Change the name I call you.\n"
        "👉 `/clear` - Quickly erase our conversation history."
    )
    await update.message.reply_markdown(settings_text, reply_markup=MAIN_MENU_MARKUP)


def format_messages_for_summary(messages: list) -> str:
    formatted_text = []
    for msg in messages:
        role = msg['role']
        content = msg['content']
        if role == "user" and " says: " in content:
            parts = content.split(" says: ", 1)
            formatted_text.append(f"User: {parts[-1]}")
        else:
            formatted_text.append(f"{role.capitalize()}: {content}")
    return "\n".join(formatted_text)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Displays a hub of deletion options to the user."""
    chat_id = update.effective_chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"User {user_display_name} opened the delete hub.")

    keyboard = [
        [InlineKeyboardButton("🗑️ Chat History", callback_data="delete_hub_history")],
        [InlineKeyboardButton("👤 User Profile", callback_data="delete_hub_profile")],
        [InlineKeyboardButton("✨ Custom Personas", callback_data="delete_hub_personas")],
        [InlineKeyboardButton("🏞️ Scenery", callback_data="delete_hub_scenery")],
        [InlineKeyboardButton("⚠️ Everything (Full Reset)", callback_data="delete_hub_everything")],
        [InlineKeyboardButton("❌ Cancel", callback_data="delete_hub_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "**Delete Hub**\n\n"
        "Select which data you would like to remove. This action is permanent.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    return DELETE_HUB

async def handle_delete_hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes a user's choice from the delete hub menu."""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    user_display_name = await get_user_display_name_from_context(update, context)
    user_logger = get_user_logger(chat_id, user_display_name)

    choice = query.data
    reply_text = ""

    if choice == 'delete_hub_history':
        if get_history_count_in_db(chat_id) > 0:
            clear_history_in_db(chat_id)
            context.user_data['current_conversation_tokens'] = 0
            user_logger.info(f"User {user_display_name} deleted their chat history.")
            reply_text = "✅ Your chat history has been deleted."
        else:
            reply_text = "ℹ️ You have no chat history to delete."

    elif choice == 'delete_hub_profile':
        if context.user_data.pop('user_profile', None):
            user_logger.info(f"User {user_display_name} deleted their profile.")
            reply_text = "✅ Your user profile has been deleted."
        else:
            reply_text = "ℹ️ You do not have a user profile to delete."

    elif choice == 'delete_hub_personas':
        custom_personas = context.user_data.get('user_custom_personas', {})
        if custom_personas:
            active_persona = context.user_data.get('llm_persona_name')
            # Check if active persona was a custom one
            if active_persona in custom_personas:
                context.user_data['llm_system_prompt'] = DEFAULT_LM_STUDIO_SYSTEM_PROMPT
                context.user_data['llm_persona_name'] = "Default"
                reply_text = "✅ Your custom personas have been deleted. You have been switched back to the 'Default' persona."
            else:
                reply_text = "✅ Your custom personas have been deleted."
            
            context.user_data.pop('user_custom_personas', None)
            user_logger.info(f"User {user_display_name} deleted their custom personas.")
        else:
            reply_text = "ℹ️ You do not have any custom personas to delete."
            
    elif choice == 'delete_hub_scenery':
        if context.user_data.pop('current_scenery_name', None):
            context.user_data.pop('current_scenery_description', None)
            user_logger.info(f"User {user_display_name} deleted their scenery.")
            reply_text = "✅ Your scenery settings have been reset."
        else:
            reply_text = "ℹ️ You do not have any scenery settings to delete."

    elif choice == 'delete_hub_everything':
        clear_history_in_db(chat_id)
        context.user_data.clear()
        user_logger.info(f"User {user_display_name} confirmed and deleted all their data.")
        reply_text = (
            "✅ **All your data has been permanently deleted.**\n\n"
            "If you want to chat again, please use the /start command to begin a new setup."
        )

    elif choice == 'delete_hub_cancel':
        user_logger.info(f"User {user_display_name} cancelled deletion.")
        reply_text = "👍 Deletion cancelled."

    await query.edit_message_text(text=reply_text, parse_mode='Markdown')
    await send_main_menu(query, context)
    return ConversationHandler.END


async def _get_contextual_system_prompt(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, str]:
    """Builds the full system prompt including profile and scenery."""
    base_system_prompt = context.user_data.get('llm_system_prompt', DEFAULT_LM_STUDIO_SYSTEM_PROMPT)
    
    user_display_name = context.user_data.get('user_display_name', 'user')
    user_profile = context.user_data.get('user_profile', 'Not specified.')
    current_scenery_desc = context.user_data.get('current_scenery_description', 'An empty, non-descript room.')

    contextual_prompt = (
        f"{base_system_prompt}\n\n"
        "--- INTERACTION CONTEXT ---\n"
        f"You are interacting with: {user_display_name}.\n"
        f"Their profile/character description is: '{user_profile}'.\n"
        f"The current scene/environment for your interaction is: '{current_scenery_desc}'.\n"
        "Integrate these elements seamlessly into your roleplay and responses. The scene is your shared environment, and the user's profile is who they are in this narrative. Always remember this context."
        "\n--- END CONTEXT ---"
    )
    return contextual_prompt, user_display_name

async def chat_with_lm_studio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    original_user_message = update.message.text
    chat_id = update.effective_chat.id

    if 'user_display_name' not in context.user_data:
        await update.message.reply_text("Welcome! Please use the /start command to get set up before we can chat.")
        return

    current_system_prompt, user_display_name = await _get_contextual_system_prompt(context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"Received message from {user_display_name}: {original_user_message}")

    processed_user_message = original_user_message

    current_tokens = context.user_data.get('current_conversation_tokens', 0)
    if current_tokens >= MAX_CONVERSATION_TOKENS:
        await update.message.reply_text(
            f"🚫 Our conversation has reached its maximum token limit ({MAX_CONVERSATION_TOKENS} tokens).\n\n"
            "Please use `/clear` or the `🗑️ Delete Data` button to start a fresh conversation.",
            reply_markup=MAIN_MENU_MARKUP
        )
        return

    messages_for_api = [{"role": "system", "content": current_system_prompt}]
    conversation_history_from_db = get_history_from_db(chat_id, limit=MAX_HISTORY_MESSAGES)
    messages_for_api.extend(conversation_history_from_db)

    context.user_data['last_user_message_context'] = {
        "chat_id": chat_id,
        "content": processed_user_message,
        "system_prompt_at_time": current_system_prompt,
    }

    current_user_message_dict = {"role": "user", "content": f"{user_display_name} says: {processed_user_message}"}
    messages_for_api.append(current_user_message_dict)

    user_logger.debug(f"Sending to LM Studio (with context):\nSystem Prompt: {current_system_prompt}\nUser Message: {current_user_message_dict['content']}")

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        response_message = await update.message.reply_text("Thinking...")

        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL_NAME, messages=messages_for_api, stream=True
        )
        
        lm_studio_response = ""
        
        for chunk in completion:
            if chunk.choices[0].delta.content:
                lm_studio_response += chunk.choices[0].delta.content
                if len(lm_studio_response) % 25 == 0: # Slightly increased chunk size
                    try:
                        await response_message.edit_text(lm_studio_response + "▌")
                        await asyncio.sleep(0.05) # Prevent rate-limiting
                    except Exception as edit_error:
                        user_logger.warning(f"Failed to edit message during streaming: {edit_error}")
        
        if lm_studio_response:
            await response_message.edit_text(lm_studio_response)
        else:
            await response_message.edit_text("Sorry, I didn't get a clear response from the AI.")

        add_message_to_db(chat_id, current_user_message_dict['role'], current_user_message_dict['content'])
        add_message_to_db(chat_id, "assistant", lm_studio_response)

        if get_history_count_in_db(chat_id) > MAX_HISTORY_MESSAGES:
            trim_history_in_db(chat_id, keep_latest=MAX_HISTORY_MESSAGES)

    except (APITimeoutError, APIConnectionError, APIStatusError, Exception) as e:
        user_logger.critical(f"Error in chat_with_lm_studio: {e}", exc_info=True)
        await update.message.reply_text("An error occurred while talking to the AI. Please try again.", reply_markup=MAIN_MENU_MARKUP)

async def regenerate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current_system_prompt, user_display_name = await _get_contextual_system_prompt(context)
    user_logger = get_user_logger(chat_id, user_display_name)
    user_logger.info(f"User {user_display_name} initiated /regenerate.")

    last_user_message_data = context.user_data.get('last_user_message_context')

    if not last_user_message_data or last_user_message_data["chat_id"] != chat_id:
        await update.message.reply_text("I don't have a previous message to regenerate.", reply_markup=MAIN_MENU_MARKUP)
        return

    delete_last_interaction_from_db(chat_id)

    processed_user_message = last_user_message_data['content']
    
    messages_for_api = [{"role": "system", "content": current_system_prompt}]
    restored_history = get_history_from_db(chat_id, limit=MAX_HISTORY_MESSAGES)
    messages_for_api.extend(restored_history)

    current_user_message_dict = {"role": "user", "content": f"{user_display_name} says: {processed_user_message}"}
    messages_for_api.append(current_user_message_dict)

    user_logger.info(f"Sending to LM Studio for regeneration: {len(messages_for_api)} messages")

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        response_message = await update.message.reply_text("Regenerating response...")

        completion = lm_studio_client.chat.completions.create(
            model=LM_STUDIO_MODEL_NAME, messages=messages_for_api, stream=True
        )

        lm_studio_response = ""
        for chunk in completion:
                 if chunk.choices[0].delta.content:
                    lm_studio_response += chunk.choices[0].delta.content
                    if len(lm_studio_response) % 25 == 0:
                        try:
                            await response_message.edit_text(lm_studio_response + "▌")
                            await asyncio.sleep(0.05)
                        except Exception as edit_error:
                            user_logger.warning(f"Failed to edit message during streaming: {edit_error}")

        if lm_studio_response:
            await response_message.edit_text(lm_studio_response)
        else:
            await response_message.edit_text("Sorry, I didn't get a clear regenerated response.")

        add_message_to_db(chat_id, current_user_message_dict['role'], current_user_message_dict['content'])
        add_message_to_db(chat_id, "assistant", lm_studio_response)

    except (APITimeoutError, APIConnectionError, APIStatusError, Exception) as e:
        user_logger.critical(f"Error in regenerate_command: {e}", exc_info=True)
        await update.message.reply_text("An error occurred during regeneration.", reply_markup=MAIN_MENU_MARKUP)

def main():
    init_db()

    persistence = PicklePersistence(filepath=os.path.join(PERSISTENCE_DIR, "bot_data.pickle"))
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    
    # --- Fallback handlers for main menu to work inside conversations ---
    fallback_handlers = [
        CommandHandler("cancel", cancel_command),
        MessageHandler(filters.Regex('^❓ Help$'), help_command),
        MessageHandler(filters.Regex('^🔄 Regenerate$'), regenerate_command),
        MessageHandler(filters.Regex('^⚙️ Settings$'), settings_command),
        MessageHandler(filters.Regex('^🗑️ Delete Data$'), delete_command),
    ]

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
            CommandHandler("persona", set_persona_command),
            CommandHandler("custom_persona", custom_persona_command),
            MessageHandler(filters.Regex('^(🗑️ Delete Data|/delete)$'), delete_command),
            CommandHandler("change_name", change_username_command),
            CommandHandler("profile", change_profile_command),
            CommandHandler("scenery", change_scenery_command),
        ],
        states={
            # Initial Setup Flow
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            ASK_PROFILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profile)],
            ASK_SCENERY: [CallbackQueryHandler(scenery_chosen_callback, pattern="^select_scenery_")],
            AWAIT_CUSTOM_SCENERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_scenery)],
            
            # Change Flows
            CHANGE_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_username)],
            CHANGE_PROFILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_profile)],
            
            # Persona Flows
            CHOOSING_PERSONA: [
                CallbackQueryHandler(persona_chosen_callback, pattern="^select_persona_"),
                CallbackQueryHandler(manage_custom_personas_menu, pattern="^manage_custom_personas$"),
            ],
            ASK_CUSTOM_PERSONA_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_custom_system_prompt)],
            ASK_CUSTOM_SYSTEM_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_custom_persona)],
            CONFIRM_DELETE_PERSONA: [
                CallbackQueryHandler(handle_delete_persona_callback),
            ],

            # Deletion Flow
            DELETE_HUB: [
                CallbackQueryHandler(handle_delete_hub_callback)
            ],
        },
        fallbacks=fallback_handlers,
        allow_reentry=True
    )
    application.add_handler(conv_handler)

    # --- Main command handlers using Regex for buttons ---
    application.add_handler(MessageHandler(filters.Regex('^(❓ Help|/help)$'), help_command))
    application.add_handler(MessageHandler(filters.Regex('^(🔄 Regenerate|/regenerate)$'), regenerate_command))
    application.add_handler(MessageHandler(filters.Regex('^(⚙️ Settings|/settings)$'), settings_command))
    application.add_handler(CommandHandler("clear", clear_history))


    # --- Main chat handler must ignore all button presses ---
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Entity(MessageEntity.URL) &
        ~filters.Text(MAIN_MENU_BUTTONS),
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