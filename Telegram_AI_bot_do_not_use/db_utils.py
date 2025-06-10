# db_utils.py
"""
Database utility module for the Telegram AI Bot.
This version includes functions for long-term memory management.
"""

import sqlite3
import logging
import sys

import config

logger = logging.getLogger(__name__)

def init_db():
    """Initializes the database and creates all necessary tables."""
    try:
        with sqlite3.connect(config.CONVERSATION_DB_FILE) as con:
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
            cur.execute('''
                CREATE TABLE IF NOT EXISTS long_term_memory (
                    chat_id INTEGER PRIMARY KEY,
                    summary TEXT NOT NULL
                )
            ''')
            con.commit()
        logger.info(f"Database initialized successfully at {config.CONVERSATION_DB_FILE}")
    except sqlite3.Error as e:
        logger.critical(f"Database initialization failed: {e}", exc_info=True)
        sys.exit(1)

def add_message_to_db(chat_id: int, role: str, content: str):
    """Adds a single message to the database."""
    try:
        with sqlite3.connect(config.CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("INSERT INTO conversations (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
            con.commit()
    except sqlite3.Error as e:
        logger.error(f"Failed to add message to DB for chat {chat_id}: {e}", exc_info=True)

def get_history_from_db(chat_id: int, limit: int) -> tuple[list, int]:
    """Retrieves conversation history and total message count for a specific chat."""
    history = []
    total_messages = 0
    try:
        with sqlite3.connect(config.CONVERSATION_DB_FILE) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM conversations WHERE chat_id = ?", (chat_id,))
            total_messages = cur.fetchone()[0]
            if limit == 0:
                 query = "SELECT role, content FROM conversations WHERE chat_id = ? ORDER BY id ASC"
                 cur.execute(query, (chat_id,))
            else:
                query = """
                SELECT role, content FROM (
                    SELECT * FROM conversations
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """
                cur.execute(query, (chat_id, limit))
            history = [{"role": row["role"], "content": row["content"]} for row in cur.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Failed to get history from DB for chat {chat_id}: {e}", exc_info=True)
    return history, total_messages

def clear_history_in_db(chat_id: int):
    """Deletes all messages and memory for a specific chat_id."""
    try:
        with sqlite3.connect(config.CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("DELETE FROM conversations WHERE chat_id = ?", (chat_id,))
            cur.execute("DELETE FROM long_term_memory WHERE chat_id = ?", (chat_id,))
            con.commit()
            logger.info(f"DB history and memory cleared for chat_id: {chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Failed to clear history in DB for chat {chat_id}: {e}", exc_info=True)

def delete_last_interaction_from_db(chat_id: int):
    """Removes the last two messages (user and assistant) for regeneration."""
    try:
        with sqlite3.connect(config.CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("""
                DELETE FROM conversations
                WHERE id IN (
                    SELECT id FROM conversations
                    WHERE chat_id = ? ORDER BY id DESC LIMIT 2
                )
            """, (chat_id,))
            con.commit()
            logger.info(f"Deleted last interaction from DB for chat_id {chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Failed to delete last interaction from DB for chat {chat_id}: {e}", exc_info=True)

def get_summary(chat_id: int) -> str | None:
    """Retrieves the long-term memory summary for a specific chat."""
    summary = None
    try:
        with sqlite3.connect(config.CONVERSATION_DB_FILE) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("SELECT summary FROM long_term_memory WHERE chat_id = ?", (chat_id,))
            row = cur.fetchone()
            if row:
                summary = row["summary"]
    except sqlite3.Error as e:
        logger.error(f"Failed to get summary from DB for chat {chat_id}: {e}", exc_info=True)
    return summary

def update_summary(chat_id: int, new_summary_text: str):
    """Updates or inserts the long-term memory summary for a specific chat."""
    try:
        with sqlite3.connect(config.CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO long_term_memory (chat_id, summary) VALUES (?, ?)",
                (chat_id, new_summary_text)
            )
            con.commit()
        logger.info(f"Updated summary in DB for chat {chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Failed to update summary in DB for chat {chat_id}: {e}", exc_info=True)