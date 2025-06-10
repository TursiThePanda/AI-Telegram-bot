# bot.py
"""
This is the final, stable, and complete version of the bot script.
It uses a global queue defined in handlers.py for maximum stability.
"""
import logging
import os
import sys
import asyncio
from logging.handlers import RotatingFileHandler
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, PicklePersistence, filters
)
import config
import db_utils
import handlers

logger = logging.getLogger(__name__)

# --- GLOBAL VARIABLE FOR AI WORKER TASK ---
# This task will be stored globally to avoid issues with PicklePersistence
ai_worker_global_task = None

def setup_logging():
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    os.makedirs(config.USER_LOGS_DIR, exist_ok=True)
    os.makedirs(config.DB_DIR, exist_ok=True)
    os.makedirs(config.PERSISTENCE_DIR, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
    log_file_path = os.path.join(config.LOGS_DIR, "bot_activity.log")
    file_handler = RotatingFileHandler(log_file_path, maxBytes=5*1024*1024, backupCount=5)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(file_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)

def main():
    """Initializes and runs the Telegram bot application."""
    setup_logging()
    if not config.TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not found. Exiting.")
        sys.exit(1)
    db_utils.init_db()
    
    persistence = PicklePersistence(filepath=os.path.join(config.PERSISTENCE_DIR, "bot_persistence.pickle"))

    # This hook starts the worker.
    async def post_init_callback(application):
        global ai_worker_global_task
        logger.info("Scheduling AI request worker...")
        # Store the task in a global variable instead of bot_data
        ai_worker_global_task = asyncio.create_task(handlers.ai_worker(application))

    # This hook stops the worker gracefully when the application is shutting down
    async def post_shutdown_callback(application):
        global ai_worker_global_task
        logger.info("Stopping AI request worker...")
        if ai_worker_global_task: # Check if the global task exists
            task = ai_worker_global_task
            task.cancel()  # Request the task to be cancelled
            try:
                # Wait for the task to finish, with a timeout
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.CancelledError:
                logger.info("AI worker task successfully cancelled.")
            except asyncio.TimeoutError:
                logger.warning("AI worker task did not stop gracefully within timeout.")
            except Exception as e:
                logger.error(f"Error during AI worker task shutdown: {e}")
            finally:
                # Clear the global reference after cancellation attempt
                ai_worker_global_task = None 

        # Ensure the queue is empty before proceeding with full shutdown
        while not handlers.REQUEST_QUEUE.empty():
            try:
                handlers.REQUEST_QUEUE.get_nowait()
                handlers.REQUEST_QUEUE.task_done()
            except asyncio.QueueEmpty:
                break
        logger.info("AI request worker stopped.")


    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init_callback)
        .post_shutdown(post_shutdown_callback)
        .build()
    )
    
    # --- Handlers Setup ---
    fallback_handlers = [
        CommandHandler("start", handlers.start_command),
        CommandHandler("setup", handlers.setup_hub_command),
        CommandHandler("help", handlers.help_command),
        CommandHandler("about", handlers.about_command),
        CommandHandler("regenerate", handlers.regenerate_command),
        CommandHandler("display_current_setup", handlers.display_current_setup_command),
        CommandHandler("clear", handlers.clear_history),
        CommandHandler("delete", handlers.delete_data_handler),
        CommandHandler("cancel", handlers.cancel_command),
    ]
    
    persona_generation_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.surprise_persona_start, pattern="^persona_surprise$")],
        states={
            config.GENERATE_PERSONA_CATEGORY: [CallbackQueryHandler(handlers.ask_persona_species_type, pattern="^persona_gen_")],
            config.ASK_PERSONA_SPECIES_TYPE: [CallbackQueryHandler(handlers.ask_persona_gender, pattern="^persona_set_species_")],
            config.ASK_PERSONA_GENDER: [CallbackQueryHandler(handlers.ask_persona_nsfw_role, pattern="^persona_set_gender_")],
            config.ASK_PERSONA_NSFW_ROLE: [CallbackQueryHandler(handlers.generate_surprise_persona, pattern="^persona_set_role_")],
        },
        fallbacks=[CallbackQueryHandler(handlers.persona_handler, pattern="^back_to_persona_list$"), *fallback_handlers],
        map_to_parent={ConversationHandler.END: config.CHOOSING_PERSONA},
        per_user=True, per_chat=True, allow_reentry=True
    )

    main_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", handlers.start_command),
            CommandHandler("delete", handlers.delete_data_handler),
            CallbackQueryHandler(handlers.change_name_handler, pattern="^setup_name$"),
            CallbackQueryHandler(handlers.change_profile_handler, pattern="^setup_profile$"),
            CallbackQueryHandler(handlers.persona_handler, pattern="^setup_persona$"),
            CallbackQueryHandler(handlers.scenery_handler, pattern="^setup_scenery$"),
        ],
        states={
            config.START_SETUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.receive_name_for_setup)],
            config.ASK_PROFILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.receive_profile_for_setup)],
            config.CHANGE_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.receive_new_name)],
            config.CHANGE_PROFILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.receive_new_profile)],
            config.ASK_SCENERY: [
                CallbackQueryHandler(handlers.receive_scenery_choice, pattern="^scenery_"),
                CallbackQueryHandler(handlers.ask_scene_genre, pattern="^generate_scene$"),
                CallbackQueryHandler(handlers.setup_hub_command, pattern="^setup_hub$")
            ],
            config.ASK_SCENE_GENRE: [CallbackQueryHandler(handlers.generate_surprise_scene, pattern="^scene_gen_")],
            config.CHOOSING_PERSONA: [
                persona_generation_conv,
                CallbackQueryHandler(handlers.custom_persona_command, pattern="^setup_custom_persona$"),
                CallbackQueryHandler(handlers.receive_persona_choice, pattern="^persona_"),
                CallbackQueryHandler(handlers.setup_hub_command, pattern="^setup_hub$"),
            ],
            config.ASK_CUSTOM_PERSONA_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.ask_custom_persona_prompt)],
            config.ASK_CUSTOM_SYSTEM_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.save_custom_persona)],
            config.DELETE_HUB: [CallbackQueryHandler(handlers.delete_data_choice, pattern="^del_")],
        },
        fallbacks=fallback_handlers,
        per_user=True, per_chat=True, allow_reentry=True
    )

    application.add_handler(main_conv_handler)
    application.add_handlers(fallback_handlers)
    
    # Add standalone handlers for generated content
    application.add_handler(CallbackQueryHandler(handlers.use_generated_scene_handler, pattern="^use_generated_scene$"))
    application.add_handler(CallbackQueryHandler(handlers.use_generated_persona, pattern="^use_generated_persona$"))
    application.add_handler(CallbackQueryHandler(handlers.scenery_handler, pattern="^back_to_scenery_list$"))
    application.add_handler(CallbackQueryHandler(handlers.persona_handler, pattern="^back_to_persona_list$"))

    application.add_handler(CallbackQueryHandler(handlers.setup_hub_command, pattern="^setup_hub$"))
    application.add_handler(CallbackQueryHandler(handlers.toggle_memory_handler, pattern="^toggle_memory$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.chat_handler))

    logger.info("Bot starting...")
    application.run_polling(drop_pending_updates=True, close_loop=False) # Keep loop open for worker cleanup

if __name__ == '__main__':
    main()