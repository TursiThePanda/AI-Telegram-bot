# log_utils.py
"""
This module provides a dedicated utility for per-user interaction logging.
"""
import os
from datetime import datetime
from telegram import User
import config

def log_user_interaction(user: User, interaction_details: str):
    """
    Logs a specific interaction to a file dedicated to that user.

    Args:
        user (telegram.User): The user object from the update.
        interaction_details (str): A string describing the user's action.
    """
    if not user:
        return

    try:
        os.makedirs(config.USER_LOGS_DIR, exist_ok=True)
        log_file_path = os.path.join(config.USER_LOGS_DIR, f"{user.id}.log")

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] - User: {user.full_name} ({user.id}) - Action: {interaction_details}\n"

        with open(log_file_path, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to write to user-specific log for user {user.id}: {e}")