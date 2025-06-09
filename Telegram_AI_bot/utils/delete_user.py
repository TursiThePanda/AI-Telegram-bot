# delete_user.py
import sqlite3
import os
import pickle
import sys

# --- Configuration ---
# Ensure these paths match the ones in your main bot script
DB_DIR = "database"
PERSISTENCE_DIR = "persistence"
CONVERSATION_DB_FILE = os.path.join(DB_DIR, "conversation_history.db")
PERSISTENCE_FILE = os.path.join(PERSISTENCE_DIR, "bot_data.pickle")


def delete_from_sqlite(chat_id_to_delete: int):
    """Deletes all conversation history for a given chat_id from the database."""
    try:
        with sqlite3.connect(CONVERSATION_DB_FILE) as con:
            cur = con.cursor()
            cur.execute("DELETE FROM conversations WHERE chat_id = ?", (chat_id_to_delete,))
            con.commit()
            # Check how many rows were deleted
            changes = con.total_changes
            if changes > 0:
                print(f"[SUCCESS] Deleted {changes} message(s) for chat_id {chat_id_to_delete} from SQLite database.")
            else:
                print(f"[INFO] No message history found for chat_id {chat_id_to_delete} in the database.")
    except sqlite3.Error as e:
        print(f"[ERROR] Could not delete from SQLite database: {e}")
        
def delete_from_pickle(chat_id_to_delete: int):
    """Deletes all user_data and chat_data for a given chat_id from the persistence file."""
    if not os.path.exists(PERSISTENCE_FILE):
        print(f"[INFO] Persistence file not found at {PERSISTENCE_FILE}. Nothing to do.")
        return

    try:
        with open(PERSISTENCE_FILE, 'rb') as f:
            persistence_data = pickle.load(f)

        user_data = persistence_data.get("user_data", {})
        chat_data = persistence_data.get("chat_data", {})
        
        data_was_deleted = False
        if chat_id_to_delete in user_data:
            del user_data[chat_id_to_delete]
            data_was_deleted = True
            print(f"[SUCCESS] Deleted user_data for chat_id {chat_id_to_delete} from persistence file.")
            
        if chat_id_to_delete in chat_data:
            del chat_data[chat_id_to_delete]
            data_was_deleted = True
            print(f"[SUCCESS] Deleted chat_data for chat_id {chat_id_to_delete} from persistence file.")

        if not data_was_deleted:
            print(f"[INFO] No user_data or chat_data found for chat_id {chat_id_to_delete} in the persistence file.")
            return

        # Write the modified data back to the file
        with open(PERSISTENCE_FILE, 'wb') as f:
            pickle.dump(persistence_data, f)
            
    except (pickle.UnpicklingError, EOFError, FileNotFoundError, Exception) as e:
        print(f"[ERROR] Could not process persistence file: {e}")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python delete_user.py <CHAT_ID_TO_DELETE>")
        sys.exit(1)
        
    try:
        target_chat_id = int(sys.argv[1])
    except ValueError:
        print("Error: Chat ID must be an integer.")
        sys.exit(1)

    print("-" * 30)
    print(f"Starting deletion process for chat_id: {target_chat_id}")
    print("-" * 30)
    
    # Run the deletion functions
    delete_from_sqlite(target_chat_id)
    delete_from_pickle(target_chat_id)
    
    print("-" * 30)
    print("Process finished.")
    print("-" * 30)
