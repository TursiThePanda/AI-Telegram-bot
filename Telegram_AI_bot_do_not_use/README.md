AI Role-Play Companion Bot

This is an advanced, conversational AI Telegram bot designed for immersive and persistent role-playing experiences. It is powered by a local Large Language Model (LLM) via LM Studio and features a robust set of customization options, including dynamic persona selection, scene setting, and a long-term memory system.
✨ Features

    🤖 Dynamic AI Persona: Choose from a wide range of pre-defined SFW and NSFW personas, or create and save your own custom characters for the AI to role-play as.
    🎲 On-Demand Content Generation: The bot can generate unique, AI-written personas and scene descriptions on the fly, across various genres from fantasy to cyberpunk.
    🧠 Long-Term Memory: The bot remembers key details from your conversations. Every 15 messages, it automatically consolidates the chat into a summary to maintain context over long periods. This feature can be toggled on or off.
    ⚙️ Full Customization: You have complete control over the experience. Define your own character's name and profile, select the AI's persona, and set the scene for the role-play, all from an easy-to-use /setup menu.
    🔐 Data Persistence & Privacy: The bot saves chat history and user settings, allowing sessions to be picked up later. It also includes tools for users to permanently delete their data.
    📝 Dedicated Logging: The bot maintains both a general activity log and separate, user-specific logs for tracking interactions.

🔧 Setup & Installation

To get the bot running, follow these steps:

    Prerequisites:
        Python 3.10+
        An active LM Studio server with a loaded model.
        A Telegram Bot Token from BotFather.

    Clone the Repository:
    Bash

git clone https://github.com/your-username/your-repository-name.git
cd your-repository-name

Install Dependencies:
It is highly recommended to use a virtual environment.
Bash

python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
pip install -r requirements.txt

(Note: A requirements.txt file should be created based on the libraries used, such as python-telegram-bot, openai, httpx, and python-dotenv.)

Configure Environment Variables:
Create a file named .env in the root directory of the project and add the following credentials:
Code snippet

TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN_HERE"
LM_STUDIO_API_BASE="http://localhost:1234/v1"
TELEGRAM_BOT_USERNAME="@YourBotUsername"

Run the Bot:
Bash

    python bot.py

📂 File Structure

The project is organized into several modules, each with a specific responsibility:

    bot.py: The main entry point of the application. It initializes the bot, sets up logging, builds the application with persistence, and registers all the command and conversation handlers.
    config.py: Contains all static configurations, including directory paths, conversation state enums, API endpoints, and the predefined lists of available personas and sceneries.
    handlers.py: The core logic of the bot. It contains all the callback functions for commands, messages, and inline button presses. It also manages the AI request queue and includes the ai_worker that processes all requests sent to the LLM.
    db_utils.py: A utility module for all database interactions. It handles the creation of the SQLite database and tables, as well as adding, retrieving, and deleting chat history and long-term memory summaries.
    log_utils.py: A dedicated utility for managing per-user interaction logs, ensuring that each user's actions are recorded in a separate file for easy tracking.
    .env: (User-created) Stores secret credentials like API keys and tokens.
    requirements.txt: (User-created) Lists all the Python dependencies required for the project.

🤖 Bot Commands

Interact with the bot using these commands in your private chat:

    /start - 💬 Starts a new chat session, clearing any previous conversation history. It will initiate the character setup process for new users.
    /setup - ⚙️ Opens the main configuration hub where you can change your name, profile, the AI's persona, the scenery, and toggle long-term memory.
    /help - ❓ Displays a list of all available commands and their descriptions.
    /about - ℹ️ Provides a detailed explanation of the bot's features, including its AI generation and memory capabilities.
    /regenerate - 🔄 Deletes the AI's last response and generates a new one based on your previous message.
    /display_current_setup - 👀 Shows a summary of all your current settings, including your character profile and the active AI persona and scenery.
    /clear - 🧹 Clears the chat history and long-term memory for the current chat.
    /delete - 🗑️ Opens a menu to permanently delete your data, including your profile, custom personas, or all data associated with your account.
    /cancel - ❌ Cancels any ongoing operation or conversation, such as creating a custom persona.