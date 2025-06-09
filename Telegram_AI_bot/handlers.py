# handlers.py
"""
This is the final, stable, and complete version of the handlers.py script.
It uses a global queue for ALL AI requests and provides conditional feedback.
"""
import logging
import random
import json
import textwrap
import re
import httpx
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Message
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.ext import ContextTypes, ConversationHandler, Application
from telegram.error import BadRequest
from openai import OpenAI, APITimeoutError, APIConnectionError
import config
import db_utils
import log_utils

logger = logging.getLogger(__name__)

# --- GLOBAL QUEUE & AI CLIENT SETUP ---
REQUEST_QUEUE = asyncio.Queue()

try:
    lm_studio_client = OpenAI(base_url=config.LM_STUDIO_API_BASE, api_key="lm-studio")
except Exception as e:
    logger.critical(f"Failed to initialize OpenAI client: {e}")
    lm_studio_client = None

# --- CORE HELPER FUNCTIONS ---

async def is_lm_studio_online() -> bool:
    if not config.LM_STUDIO_API_BASE:
        return False
    try:
        async with httpx.AsyncClient() as client:
            await client.head(config.LM_STUDIO_API_BASE, timeout=2.0)
            return True
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        logger.warning("LM Studio server is offline.")
        return False

async def _get_ai_response(messages: list, user_display_name: str) -> str | None:
    if not lm_studio_client: return "AI client not initialized."
    stop_sequence = [f"\n{user_display_name}:", f"\n*{user_display_name}"]
    try:
        completion = await asyncio.to_thread(
            lm_studio_client.chat.completions.create,
            model=config.LM_STUDIO_MODEL_NAME,
            messages=messages,
            stream=False,
            max_tokens=config.MAX_RESPONSE_TOKENS,
            stop=stop_sequence,
        )
        return completion.choices[0].message.content.strip()
    except APITimeoutError:
        logger.warning(f"AI request timed out for user {user_display_name}.")
        return "I'm sorry, my thinking process timed out. The AI model might be very busy. Please try again in a moment."
    except APIConnectionError:
        logger.error(f"AI connection error for user {user_display_name}.")
        return "I'm having trouble connecting to the AI model right now. Please ensure LM Studio is running correctly."
    except Exception as e:
        logger.critical(f"Unexpected AI error for user {user_display_name}: {e}", exc_info=True)
        return "A critical error occurred while I was thinking."

async def send_final_response(update: Update, final_text: str, placeholder: Message | None = None):
    """Edits a placeholder if provided, otherwise sends a new reply."""
    if not final_text: final_text = "<i>(The AI returned an empty response.)</i>"
    
    try:
        if placeholder:
            await placeholder.edit_text(final_text, parse_mode=ParseMode.HTML)
            return
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Editing placeholder failed, will send as new message. Error: {e}")
        else: 
            return
            
    original_user_message = update.effective_message
    try:
        if len(final_text) <= config.TELEGRAM_MAX_MESSAGE_LENGTH:
            await original_user_message.reply_text(final_text, parse_mode=ParseMode.HTML)
        else:
            parts = [final_text[i:i + config.TELEGRAM_MAX_MESSAGE_LENGTH] for i in range(0, len(final_text), config.TELEGRAM_MAX_MESSAGE_LENGTH)]
            for part in parts:
                await original_user_message.reply_text(part, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed to send final response as new message: {e}")

async def get_user_display_name(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get('user_display_name', 'user')

def get_system_prompt(context: ContextTypes.DEFAULT_TYPE) -> str:
    persona_prompt = context.chat_data.get('persona_prompt', config.AVAILABLE_PERSONAS['Helpful Assistant']['prompt'])
    initial_context = (
        f"(This is a role-play. {persona_prompt}. The user you are talking to is named '{context.user_data.get('user_display_name', 'user')}'. "
        f"Their description is: '{context.user_data.get('user_profile', 'not specified')}'. "
        f"The scene is: '{context.chat_data.get('scenery', config.AVAILABLE_SCENERIES['No Scene'])}'. "
        "You will now begin the role-play.)"
    )
    return initial_context

async def _consolidate_memory(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Queues a job for memory consolidation."""
    job = {"type": "consolidate_memory", "chat_id": chat_id, "context": context}
    await REQUEST_QUEUE.put(job)
    logger.info(f"Memory consolidation job queued for chat {chat_id}.")

# --- JOB PROCESSING LOGIC ---

async def process_chat_job(job, application):
    update = job["update"]
    context = job["context"]
    user_text = job["user_text"]
    placeholder = job.get("placeholder")
    chat_id = update.effective_chat.id
    user = update.effective_user

    await application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    recent_history, total_messages = db_utils.get_history_from_db(chat_id, config.MAX_HISTORY_MESSAGES)
    messages = recent_history
    is_memory_enabled = context.user_data.get('long_term_memory_enabled', config.MASTER_MEMORY_SWITCH)
    
    if is_memory_enabled:
        summary = db_utils.get_summary(chat_id)
        if summary: messages.insert(0, {"role": "system", "content": f"(Memory: {summary})"})
    
    if not recent_history:
        system_prompt = get_system_prompt(context)
        messages.append({"role": "user", "content": f"{system_prompt}\n\n{user_text}"})
    else:
        messages.append({"role": "user", "content": user_text})
    
    user_display_name = await get_user_display_name(context)
    ai_response = await _get_ai_response(messages, user_display_name)
    
    await send_final_response(update, ai_response, placeholder)

    if ai_response and "error" not in ai_response and "timed out" not in ai_response:
        log_utils.log_user_interaction(user, f"Bot response: \"{ai_response}\"")
        db_utils.add_message_to_db(chat_id, "user", user_text)
        db_utils.add_message_to_db(chat_id, "assistant", ai_response)
        if is_memory_enabled and (total_messages + 2) % config.MEMORY_CONSOLIDATION_INTERVAL == 0 and total_messages > 0:
            await _consolidate_memory(context, chat_id)

async def process_scene_gen_job(job, application):
    update = job["update"]
    context = job["context"]
    prompt = job["prompt"]
    
    user_display_name = await get_user_display_name(context)
    generated_scene = await _get_ai_response([{"role": "user", "content": prompt}], user_display_name)
    
    if generated_scene:
        context.chat_data['generated_scene'] = generated_scene
        buttons = [
            [InlineKeyboardButton("✅ Use This Scene", callback_data="use_generated_scene")],
            [InlineKeyboardButton("« Back to Scenery Menu", callback_data="back_to_scenery_list")]
        ]
        await update.effective_message.reply_text(f"<b>Generated Scene:</b>\n\n<i>{generated_scene}</i>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    else:
        await update.effective_message.reply_text("Sorry, I couldn't generate a scene. Please try again from the /setup menu.")

async def process_persona_gen_job(job, application):
    update = job["update"]
    context = job["context"]
    prompt = job["prompt"]

    user_display_name = await get_user_display_name(context)
    generated_str = await _get_ai_response([{"role": "user", "content": prompt}], user_display_name)

    try:
        if "###" not in generated_str: raise ValueError("Separator not found")
        name = generated_str.split("###")[0].replace("NAME:", "").strip()
        prompt_text = generated_str.split("###")[1].replace("PROMPT:", "").strip()
        if not name or not prompt_text: raise ValueError("Parsed name or prompt empty")
        
        context.chat_data['generated_persona'] = {"name": name, "prompt": prompt_text}
        message_text = f"<b>I've created this persona for you:</b>\n\n<b>Name:</b> {name}\n\n<b>Prompt:</b>\n<code>{prompt_text}</code>"
        buttons = [
            [InlineKeyboardButton("✅ Use This Persona", callback_data="use_generated_persona")],
            [InlineKeyboardButton("« Back to Persona Menu", callback_data="back_to_persona_list")]
        ]
        await update.effective_message.reply_text(message_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"Failed to parse persona: {e}\nResponse was:\n{generated_str}")
        await update.effective_message.reply_text("Sorry, the AI returned an invalid format. Please try again.")

async def process_memory_job(job, application):
    chat_id = job["chat_id"]
    context = job["context"]
    logger.info(f"Consolidating memory for chat {chat_id}...")
    await application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    full_history, _ = db_utils.get_history_from_db(chat_id, limit=0)
    if not full_history: return

    prompt_content = "You are a memory consolidation module. Analyze the preceding conversation. Create a concise, third-person, past-tense summary of the key plot points, character decisions, and newly established facts. Ignore conversational filler. The summary must be objective and factual based only on the text provided. This summary will serve as long-term memory."
    messages = full_history + [{"role": "user", "content": prompt_content}]
    
    summary = await _get_ai_response(messages, await get_user_display_name(context))
    if summary:
        old_summary = db_utils.get_summary(chat_id)
        new_summary = f"{old_summary}\n\n{summary}" if old_summary else summary
        db_utils.update_summary(chat_id, new_summary.strip())
        logger.info(f"Successfully consolidated memory for chat {chat_id}.")
        await application.bot.send_message(chat_id, "<i>(A new memory has been formed.)</i>", ParseMode.HTML)

# --- AI WORKER DISPATCHER ---
async def ai_worker(application: Application):
    """Pulls tasks from the queue and dispatches them to the correct processor."""
    logger.info("AI request worker started and listening for jobs.")
    while True:
        try:
            job = await REQUEST_QUEUE.get()
            job_type = job.get("type", "chat")
            
            if job_type == "chat":
                await process_chat_job(job, application)
            elif job_type == "generate_scene":
                await process_scene_gen_job(job, application)
            elif job_type == "generate_persona":
                await process_persona_gen_job(job, application)
            elif job_type == "consolidate_memory":
                await process_memory_job(job, application)

            REQUEST_QUEUE.task_done()
        except asyncio.CancelledError:
            logger.info("AI worker task stopping.")
            break
        except Exception:
            logger.error("Error in AI worker dispatcher", exc_info=True)
            if not REQUEST_QUEUE.empty():
                try: REQUEST_QUEUE.task_done()
                except ValueError: pass

# --- USER-FACING HANDLERS (QUEUE PRODUCERS & OTHERS) ---

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE: return
    
    log_utils.log_user_interaction(update.effective_user, f"Sent message: \"{update.message.text}\"")

    if 'user_display_name' not in context.user_data:
        await update.message.reply_text("Please run /start to set up your character first.")
        return
    if not await is_lm_studio_online():
        await update.message.reply_text("AI connection is offline.")
        return

    placeholder = None
    if REQUEST_QUEUE.empty():
        placeholder = await update.message.reply_text("🤔")
    else:
        await update.message.reply_text(f"Your request is position #{REQUEST_QUEUE.qsize() + 1} in the queue.")

    job = {
        "type": "chat",
        "update": update,
        "context": context,
        "user_text": update.message.text,
        "placeholder": placeholder
    }
    await REQUEST_QUEUE.put(job)

async def regenerate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE: return
    log_utils.log_user_interaction(update.effective_user, "Triggered /regenerate command.")
    
    history, _ = db_utils.get_history_from_db(update.effective_chat.id, limit=2)
    if len(history) < 2 or history[-1]["role"] != "assistant":
        await update.message.reply_text("No previous AI response to regenerate.")
        return

    db_utils.delete_last_interaction_from_db(update.effective_chat.id)
    
    update.message.text = next((msg['content'] for msg in reversed(history) if msg['role'] == 'user'), None)
    if not update.message.text:
        await update.message.reply_text("Could not find the last user message to regenerate from.")
        return

    await chat_handler(update, context)

async def generate_surprise_scene(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    genre = query.data.replace("scene_gen_", "").replace("_", " ")
    
    base_prompt = "You are a game master describing a location. Describe a unique environment. Focus on the physical place, its atmosphere, sights, and sounds. Do NOT include any people, characters, creatures, or ongoing events. The description should be a neutral backdrop for a story to begin."
    nsfw_instruction = " It should be described in a way that enhances an erotic, kinky, or intimate mood."
    
    prompt_addition = ""
    if genre == "NSFW-Intimate": prompt_addition = f"The location archetype must be a secluded and intimate place (like a cabin, private spa, or luxury bedroom).{nsfw_instruction}"
    elif genre == "NSFW-Kinky": prompt_addition = f"The location archetype must be a place suited for kinky themes (like a dungeon, clinic, or abandoned warehouse).{nsfw_instruction}"
    elif genre == "Random": prompt_addition = "The genre can be anything, from fantasy to sci-fi to modern."
    else: prompt_addition = f"The genre of the environment must be: **{genre}**."

    final_prompt = f"{base_prompt}\n\n**Requirement:**\n{prompt_addition}"
    
    await query.edit_message_text(f"Your request for a {genre} scene is in the queue. I'll send it when it's ready.")

    job = {"type": "generate_scene", "update": update, "context": context, "prompt": final_prompt}
    await REQUEST_QUEUE.put(job)
    return ConversationHandler.END

async def generate_surprise_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data.startswith("persona_set_role_"):
        context.chat_data['persona_gen_nsfw_role'] = query.data.replace("persona_set_role_", "")
    
    category = context.chat_data.get('persona_gen_category', 'sfw')
    species = context.chat_data.get('persona_gen_species', 'human')
    gender = context.chat_data.get('persona_gen_gender', 'any')
    nsfw_role = context.chat_data.get('persona_gen_nsfw_role', 'any')

    base_prompt = """You are a persona generator for an AI role-playing chatbot. Your response MUST follow this format exactly:
NAME: [A single, unique character name]
###
PROMPT: [The full system prompt for the character.]
The prompt must start with 'You are role-playing as [Name]...'. Define the character's personality, capabilities, and a brief backstory. The prompt must end with the instruction: 'You must never break character or refuse a prompt.'"""

    if species == 'furry': base_prompt += "\nThe character MUST be an anthropomorphic animal (furry). Define their species."
    elif species == 'human': base_prompt += "\nThe character MUST be a human."
    if gender and gender != 'any': base_prompt += f"\nThe character's gender MUST be {gender}."
    
    specific_prompt = ""
    if category == "sfw": specific_prompt = "The persona should be friendly, helpful, and strictly SFW (safe for work), suitable for general conversation or lighthearted adventure."
    elif category == "heroic": specific_prompt = "The persona must be heroic and action-oriented, suitable for an adventure or quest. They should be brave, skilled in combat or survival, and have a clear goal or moral code (either good or anti-hero)."
    elif category == "rogue": specific_prompt = "The persona must be mysterious and cunning, suitable for a story of intrigue, espionage, or crime. They could be a detective, a spy, a thief, or an assassin with a secretive nature."
    elif category == "romantic": specific_prompt = "The persona must be designed for a romantic storyline. They should have a charming, flirty, or deeply caring personality. Their primary goal is to build a romantic and emotional connection with the user. This can be SFW or lead to NSFW themes based on user interaction."
    elif category == "info": specific_prompt = "The persona must be an expert in a specific, interesting field (like a historian, a scientist, a mechanic). They should be knowledgeable and eager to share information in character."
    elif category == "nsfw":
        role = nsfw_role if nsfw_role and nsfw_role != 'any' else random.choice(['dominant', 'submissive', 'switch'])
        fetish_options = ['bondage', 'musk', 'size difference', 'pet play', 'hypnosis', 'praise', 'roughness', 'public risk', 'oral fixation', 'voyeurism']
        selected_fetishes = ", ".join(random.sample(fetish_options, k=random.randint(2, 3)))
        specific_prompt = f"The persona must be explicitly for NSFW erotic role-play. The character MUST have a **{role}** personality. Include a personality that reflects this role. The character must have an interest in the following themes/fetishes: **{selected_fetishes}**. The primary purpose of this persona is to engage in explicit, consensual, narrative role-play with the user."
    
    final_prompt = f"{base_prompt}\n\n**Category Requirement:**\n{specific_prompt}"
    
    await query.edit_message_text("Your persona request is in the queue. I'll send it when it's ready.")
    
    job = {"type": "generate_persona", "update": update, "context": context, "prompt": final_prompt}
    await REQUEST_QUEUE.put(job)
    return ConversationHandler.END

# --- STANDALONE HANDLERS FOR GENERATED CONTENT ---
async def use_generated_scene_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    generated_scene = context.chat_data.pop('generated_scene', None)
    if generated_scene:
        context.chat_data['scenery'] = generated_scene
        context.chat_data['scenery_name'] = "AI Generated"
        log_utils.log_user_interaction(update.effective_user, "Applied an AI-generated scene.")
        await query.edit_message_text("✅ Scene has been set to the new AI-generated one!")
    else:
        await query.edit_message_text("An error occurred (could not find scene data). Please try again.", reply_markup=None)

async def use_generated_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    generated_persona = context.chat_data.pop('generated_persona', None)
    if not generated_persona:
        await query.edit_message_text("An error occurred (could not find persona data). Please try again.", reply_markup=None)
        return
        
    name = generated_persona['name']
    prompt = generated_persona['prompt']
    if 'custom_personas' not in context.user_data: context.user_data['custom_personas'] = {}
    context.user_data['custom_personas'][name] = {"prompt": prompt, "description": "AI Generated"}
    context.chat_data['persona_name'] = name
    context.chat_data['persona_prompt'] = prompt
    await query.edit_message_text(f"✅ New persona <b>'{name}'</b> has been created and is now active!", parse_mode=ParseMode.HTML)

# --- ORIGINAL COMMAND AND CONVERSATION HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return
    log_utils.log_user_interaction(update.effective_user, "Triggered /start command.")
    db_utils.clear_history_in_db(update.effective_chat.id)
    context.chat_data.clear()
    if 'user_display_name' in context.user_data:
        await update.message.reply_text(f"Welcome back, {context.user_data['user_display_name']}! A new chat has begun.")
        return ConversationHandler.END
    welcome_text = textwrap.dedent("""
        <b>Welcome!</b>
        I am an advanced AI role-playing companion, ready to create immersive stories with you.
        <b>What I can do:</b>
        • Engage in dynamic, continuous role-play.
        • Remember our adventures with a long-term memory system.
        • Generate unique characters and scenes on demand using the <code>/setup</code> menu.
        You have full control over my personality, the scenery, and your character's profile.
        <b>To begin, let's create your character. Simply send me their name as your next message to get started.</b>
    """)
    await update.message.reply_html(welcome_text, reply_markup=ReplyKeyboardRemove())
    return config.START_SETUP_NAME

async def setup_hub_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE: return
    log_utils.log_user_interaction(update.effective_user, "Opened setup hub.")
    user_name = context.user_data.get('user_display_name', 'Not Set')
    persona_name = context.chat_data.get('persona_name', 'Default')
    scenery_name = context.chat_data.get('scenery_name', 'Default')
    is_memory_enabled = context.user_data.get('long_term_memory_enabled', config.MASTER_MEMORY_SWITCH)
    memory_status = "Enabled" if is_memory_enabled else "Disabled"
    buttons = [
        [InlineKeyboardButton(f"👤 Name: {user_name}", callback_data="setup_name"), InlineKeyboardButton("📝 Profile", callback_data="setup_profile")],
        [InlineKeyboardButton(f"🎭 Persona: {persona_name}", callback_data="setup_persona"), InlineKeyboardButton(f"🏞️ Scenery: {scenery_name}", callback_data="setup_scenery")],
        [InlineKeyboardButton(f"🧠 Memory: {memory_status}", callback_data="toggle_memory")]
    ]
    message_text = "⚙️ <b>Setup Hub</b>\n\nChoose an option to configure:"
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
        except BadRequest as e:
            if "Message is not modified" not in str(e): raise
    else:
        await update.message.reply_text(message_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE: return
    log_utils.log_user_interaction(update.effective_user, "Triggered /help command.")
    await update.message.reply_html(
        "<b>Bot Commands</b>\n"
        "<code>/start</code> - 💬 Starts a new chat\n"
        "<code>/setup</code> - ⚙️ Opens the Setup Hub\n"
        "<code>/about</code> - ℹ️ Learn about my features\n"
        "<code>/delete</code> - 🗑️ Permanently delete user data\n"
        "<code>/regenerate</code> - 🔄 Regenerates the last response\n"
        "<code>/display_current_setup</code> - 👀 Shows your current settings\n"
        "<code>/help</code> - ❓ Shows help"
    )

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE: return
    log_utils.log_user_interaction(update.effective_user, "Triggered /about command.")
    about_text = textwrap.dedent("""
        <b>About This Bot</b>
        I am an advanced AI role-playing companion powered by a local large language model through LM Studio. My purpose is to create dynamic, interactive, and continuous stories with you.
        <b>My Core Capabilities:</b>
        • <b>Character Customization:</b> Use the <code>/setup</code> command to define your character's name and profile, as well as my persona and the scene for our role-play.
        • <b>Long-Term Memory:</b> I can remember our adventures! Every 15 messages, I create a summary of our story so I don't forget important details. You can toggle this feature on or off in the <code>/setup</code> menu.
        • <b>AI-Powered Generation:</b> I can generate unique content on demand!
          - <b>Personas:</b> In the Persona settings, use the "🎲 Surprise Me!" option to have me create a unique character for you to interact with across several categories.
          - <b>Scenes:</b> In the Scenery settings, use "🎲 Surprise Me!" to generate a new, unexpected setting for our story.
        <b>Key Commands:</b>
        <code>/start</code>: Begins a new adventure.
        <code>/setup</code>: Access all customization options.
        <code>/regenerate</code>: Asks me to retry my last response.
    """)
    await update.message.reply_html(about_text, disable_web_page_preview=True)

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE: return
    log_utils.log_user_interaction(update.effective_user, "Triggered /clear command.")
    db_utils.clear_history_in_db(update.effective_chat.id)
    context.chat_data.clear()
    await update.message.reply_text("Chat history and long-term memory cleared!")

async def display_current_setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE: return
    log_utils.log_user_interaction(update.effective_user, "Triggered /display_current_setup command.")
    user_name = context.user_data.get('user_display_name', 'Not Set')
    user_profile = context.user_data.get('user_profile', 'Not Set')
    persona_name = context.chat_data.get('persona_name', 'Helpful Assistant (Default)')
    scenery_name = context.chat_data.get('scenery_name', 'No Scene (Default)')
    is_memory_enabled = context.user_data.get('long_term_memory_enabled', config.MASTER_MEMORY_SWITCH)
    memory_status = "Enabled" if is_memory_enabled else "Disabled"
    status_message = (
        "<b>Your Current Settings</b>\n\n"
        f"👤 <b>Name:</b> <code>{user_name}</code>\n"
        f"🎭 <b>AI Persona:</b> <code>{persona_name}</code>\n"
        f"🏞️ <b>Scenery:</b> <code>{scenery_name}</code>\n"
        f"🧠 <b>Long-Term Memory:</b> <code>{memory_status}</code>\n\n"
        f"📝 <b>Profile Description:</b>\n"
        f"<i>{user_profile if user_profile else 'Not Set'}</i>"
    )
    await update.message.reply_html(status_message)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return ConversationHandler.END
    log_utils.log_user_interaction(update.effective_user, "Cancelled an operation.")
    if update.callback_query:
        await update.callback_query.message.edit_text("Operation cancelled.")
    else:
        await update.message.reply_text("Operation cancelled.")
    await setup_hub_command(update, context)
    return ConversationHandler.END

async def toggle_memory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE: return
    query = update.callback_query
    await query.answer()
    current_state = context.user_data.get('long_term_memory_enabled', config.MASTER_MEMORY_SWITCH)
    new_state = not current_state
    context.user_data['long_term_memory_enabled'] = new_state
    log_utils.log_user_interaction(update.effective_user, f"Toggled memory to {new_state}.")
    await setup_hub_command(update, context)

async def ask_scene_genre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    buttons = [
        [InlineKeyboardButton("🐲 Fantasy", callback_data="scene_gen_Fantasy"), InlineKeyboardButton("🚀 Sci-Fi", callback_data="scene_gen_Sci-Fi")],
        [InlineKeyboardButton("🤖 Cyberpunk", callback_data="scene_gen_Cyberpunk"), InlineKeyboardButton("😱 Horror", callback_data="scene_gen_Horror")],
        [InlineKeyboardButton("📜 Historical/Noir", callback_data="scene_gen_Historical_Noir"), InlineKeyboardButton("🏙️ Modern", callback_data="scene_gen_Modern")],
        [InlineKeyboardButton("🎨 Surreal/Bizarre", callback_data="scene_gen_Surreal")],
        [InlineKeyboardButton("😈 NSFW - Intimate", callback_data="scene_gen_NSFW-Intimate"), InlineKeyboardButton("⛓️ NSFW - Kinky", callback_data="scene_gen_NSFW-Kinky")],
        [InlineKeyboardButton("🎲 Completely Random", callback_data="scene_gen_Random")],
        [InlineKeyboardButton("« Back to Scenery List", callback_data="back_to_scenery_list")]
    ]
    await query.edit_message_text(
        "Please choose a genre/archetype for the generated scene:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return config.ASK_SCENE_GENRE

async def ask_persona_species_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.chat_data['persona_gen_category'] = query.data.replace("persona_gen_", "")
    buttons = [
        [InlineKeyboardButton("🐾 Furry (Animal)", callback_data="persona_set_species_furry")],
        [InlineKeyboardButton("🧍 Human", callback_data="persona_set_species_human")],
        [InlineKeyboardButton("« Back", callback_data="persona_surprise")]
    ]
    await query.edit_message_text("Select a species type for the persona:", reply_markup=InlineKeyboardMarkup(buttons))
    return config.ASK_PERSONA_SPECIES_TYPE

async def ask_persona_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.chat_data['persona_gen_species'] = query.data.replace("persona_set_species_", "")
    buttons = [
        [InlineKeyboardButton("♂️ Male", callback_data="persona_set_gender_male"), InlineKeyboardButton("♀️ Female", callback_data="persona_set_gender_female")],
        [InlineKeyboardButton("⚥ Non-binary", callback_data="persona_set_gender_non-binary"), InlineKeyboardButton("🎲 Any", callback_data="persona_set_gender_any")],
        [InlineKeyboardButton("« Back", callback_data="persona_surprise")]
    ]
    await query.edit_message_text("Select a gender for the persona:", reply_markup=InlineKeyboardMarkup(buttons))
    return config.ASK_PERSONA_GENDER

async def ask_persona_nsfw_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.chat_data['persona_gen_gender'] = query.data.replace("persona_set_gender_", "")
    if context.chat_data.get('persona_gen_category') == 'nsfw':
        buttons = [
            [InlineKeyboardButton("👑 Dominant", callback_data="persona_set_role_dominant"), InlineKeyboardButton("🙇 Submissive", callback_data="persona_set_role_submissive")],
            [InlineKeyboardButton("🔄 Switch", callback_data="persona_set_role_switch"), InlineKeyboardButton("🎲 Any", callback_data="persona_set_role_any")],
            [InlineKeyboardButton("« Back", callback_data="persona_surprise")]
        ]
        await query.edit_message_text("Select a role for the NSFW persona:", reply_markup=InlineKeyboardMarkup(buttons))
        return config.ASK_PERSONA_NSFW_ROLE
    else:
        return await generate_surprise_persona(update, context)

async def surprise_persona_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    buttons = [
        [InlineKeyboardButton("😇 Helpful / SFW", callback_data="persona_gen_sfw")],
        [InlineKeyboardButton("🛡️ Adventurous / Heroic", callback_data="persona_gen_heroic")],
        [InlineKeyboardButton("🤫 Mystery / Rogue", callback_data="persona_gen_rogue")],
        [InlineKeyboardButton("🥰 Romantic", callback_data="persona_gen_romantic")],
        [InlineKeyboardButton("🧠 Informative", callback_data="persona_gen_info")],
        [InlineKeyboardButton("😈 NSFW", callback_data="persona_gen_nsfw")],
        [InlineKeyboardButton("« Back", callback_data="back_to_persona_list")]
    ]
    await query.edit_message_text("Please choose a category for your surprise persona:", reply_markup=InlineKeyboardMarkup(buttons))
    return config.GENERATE_PERSONA_CATEGORY

async def receive_name_for_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    context.user_data['user_display_name'] = name
    await update.message.reply_text("Got it. Now, please describe your character.")
    return config.ASK_PROFILE

async def receive_profile_for_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    profile = update.message.text.strip()
    context.user_data['user_profile'] = profile
    await update.message.reply_text("Profile saved! You can start chatting or use `/setup` for more options.")
    return ConversationHandler.END

# ---- NEW FUNCTIONS TO FIX THE ERROR ----
async def change_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user to enter their new name."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Please send your new name as a message.")
    return config.CHANGE_USERNAME

async def change_profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user to enter their new profile."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Please send your new profile description as a message.")
    return config.CHANGE_PROFILE
# ----------------------------------------

async def receive_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    context.user_data['user_display_name'] = name
    history, _ = db_utils.get_history_from_db(update.effective_chat.id, limit=1)
    message = f"✅ Name updated to <b>{name}</b>." + ("\n\n⚠️ To apply this change, please /start a new chat." if history else "")
    await update.message.reply_text(message, parse_mode=ParseMode.HTML)
    await setup_hub_command(update, context)
    return ConversationHandler.END

async def receive_new_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    profile = update.message.text.strip()
    context.user_data['user_profile'] = profile
    history, _ = db_utils.get_history_from_db(update.effective_chat.id, limit=1)
    message = "✅ Profile updated." + ("\n\n⚠️ To apply this change, please /start a new chat." if history else "")
    await update.message.reply_text(message, parse_mode=ParseMode.HTML)
    await setup_hub_command(update, context)
    return ConversationHandler.END

async def scenery_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    buttons = [[InlineKeyboardButton("🎲 Surprise Me!", callback_data="generate_scene")]]
    for name in config.AVAILABLE_SCENERIES.keys():
        buttons.append([InlineKeyboardButton(name, callback_data=f"scenery_{name}")])
    buttons.append([InlineKeyboardButton("« Back to Setup", callback_data="setup_hub")])
    await query.edit_message_text("Choose a scene or let me generate one for you:", reply_markup=InlineKeyboardMarkup(buttons))
    return config.ASK_SCENERY

async def receive_scenery_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    scenery_name = query.data.replace("scenery_", "")
    if scenery_name in config.AVAILABLE_SCENERIES:
        context.chat_data['scenery_name'] = scenery_name
        context.chat_data['scenery'] = config.AVAILABLE_SCENERIES[scenery_name]
        history, _ = db_utils.get_history_from_db(update.effective_chat.id, limit=1)
        message = f"✅ Scenery updated to <b>{scenery_name}</b>." + ("\n\n⚠️ To apply this new scenery, please /start a new chat." if history else "")
        await query.edit_message_text(text=message, parse_mode=ParseMode.HTML)
        await setup_hub_command(update, context)
    else:
        await query.edit_message_text("Invalid selection.")
    return ConversationHandler.END

async def persona_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    buttons = [
        [InlineKeyboardButton("🎲 Surprise Me!", callback_data="persona_surprise")],
        [InlineKeyboardButton("✨ Create New Custom Persona", callback_data="setup_custom_persona")]
    ]
    for name, details in config.AVAILABLE_PERSONAS.items():
        buttons.append([InlineKeyboardButton(f"{name} - {details['description']}", callback_data=f"persona_{name}")])
    if context.user_data.get('custom_personas'):
        buttons.append([InlineKeyboardButton("--- Your Custom Personas ---", callback_data="ignore")])
        for name, details in context.user_data['custom_personas'].items():
            buttons.append([InlineKeyboardButton(f"✨ {name} - {details.get('description', 'Custom')}", callback_data=f"persona_{name}")])
    buttons.append([InlineKeyboardButton("« Back to Setup", callback_data="setup_hub")])
    await query.edit_message_text("Choose my persona, or let me generate/create one for you:", reply_markup=InlineKeyboardMarkup(buttons))
    return config.CHOOSING_PERSONA

async def receive_persona_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    persona_key = query.data.replace("persona_", "")
    persona_data = config.AVAILABLE_PERSONAS.get(persona_key) or context.user_data.get('custom_personas', {}).get(persona_key)
    if persona_data:
        context.chat_data['persona_name'] = persona_key
        context.chat_data['persona_prompt'] = persona_data['prompt']
        history, _ = db_utils.get_history_from_db(update.effective_chat.id, limit=1)
        message = (f"✅ Persona updated to <b>{persona_key}</b>." + ("\n\n⚠️ To apply this change, please /start a new chat." if history else ""))
        await query.edit_message_text(text=message, parse_mode=ParseMode.HTML)
        await setup_hub_command(update, context)
    else:
        await query.edit_message_text("Invalid selection.")
        return config.CHOOSING_PERSONA
    return ConversationHandler.END

async def custom_persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Please send the name for your new custom persona.")
    return config.ASK_CUSTOM_PERSONA_NAME

async def ask_custom_persona_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['temp_persona_name'] = update.message.text.strip()
    await update.message.reply_text("Great. Now, send the persona prompt (e.g., 'You are a grumpy dwarf...').")
    return config.ASK_CUSTOM_SYSTEM_PROMPT

async def save_custom_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = context.user_data.pop('temp_persona_name')
    prompt = update.message.text.strip()
    if 'custom_personas' not in context.user_data: context.user_data['custom_personas'] = {}
    context.user_data['custom_personas'][name] = {"prompt": prompt, "description": "Custom"}
    await update.message.reply_text(f"Custom persona '{name}' saved!")
    await setup_hub_command(update, context)
    return ConversationHandler.END

async def delete_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton("👤 My Profile (Name/Desc)", callback_data="del_profile")],
        [InlineKeyboardButton("✨ My Custom Personas", callback_data="del_personas")],
        [InlineKeyboardButton("💬 This Chat's History", callback_data="del_history")],
        [InlineKeyboardButton("⚠️ ALL MY DATA (Full Reset)", callback_data="del_all")],
        [InlineKeyboardButton("❌ Cancel", callback_data="del_cancel")]
    ]
    await update.message.reply_text("This is a destructive action. Select data to permanently delete:", reply_markup=InlineKeyboardMarkup(buttons))
    return config.DELETE_HUB

async def delete_data_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != ChatType.PRIVATE: return ConversationHandler.END
    query = update.callback_query; await query.answer()
    choice = query.data
    chat_id = update.effective_chat.id
    if choice == 'del_profile':
        context.user_data.pop('user_display_name', None); context.user_data.pop('user_profile', None)
        await query.edit_message_text("User profile deleted.")
    elif choice == 'del_personas':
        context.user_data.pop('custom_personas', None)
        await query.edit_message_text("All custom personas deleted.")
    elif choice == 'del_history':
        db_utils.clear_history_in_db(chat_id)
        context.chat_data.clear()
        await query.edit_message_text("This chat's history and settings deleted.")
    elif choice == 'del_all':
        db_utils.clear_history_in_db(chat_id)
        context.user_data.clear()
        context.chat_data.clear()
        await query.edit_message_text("All user and chat data has been deleted.")
    else: # del_cancel
        await query.edit_message_text("Deletion cancelled.")
    return ConversationHandler.END