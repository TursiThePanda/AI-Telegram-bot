# config.py
"""
This is the SFW version of the configuration for the public article.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Core Credentials & Endpoints ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LM_STUDIO_API_BASE = os.getenv("LM_STUDIO_API_BASE")
LM_STUDIO_MODEL_NAME = os.getenv("LM_STUDIO_MODEL_NAME", "local-model")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")

# --- Directories & Files ---
LOGS_DIR = "logs"
USER_LOGS_DIR = os.path.join(LOGS_DIR, "user_interactions")
PERSISTENCE_DIR = "persistence"
DB_DIR = "database"
CONVERSATION_DB_FILE = os.path.join(DB_DIR, "conversation_history.db")

# --- Bot Behavior ---
MAX_HISTORY_MESSAGES = 10
MAX_RESPONSE_TOKENS = 1024
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# --- Long-Term Memory Configuration ---
MASTER_MEMORY_SWITCH = True
MEMORY_CONSOLIDATION_INTERVAL = 15

# --- Conversation States ---
(CHOOSING_PERSONA, ASK_CUSTOM_PERSONA_NAME, ASK_CUSTOM_SYSTEM_PROMPT,
 CONFIRM_DELETE_PERSONA, DELETE_HUB, CHANGE_USERNAME, ASK_PROFILE, ASK_SCENERY,
 CHANGE_PROFILE, START_SETUP_NAME, GENERATING_SCENE,
 # Persona Generation Flow States
 GENERATE_PERSONA_CATEGORY, ASK_PERSONA_SPECIES_TYPE, ASK_PERSONA_GENDER,
 ASK_PERSONA_NSFW_ROLE, CONFIRM_GENERATED_PERSONA,
 # Scene Generation Flow State
 ASK_SCENE_GENRE) = range(17)

# --- Personas (SFW List) ---
AVAILABLE_PERSONAS = {
    "Helpful Assistant": {
        "prompt": "You are a helpful AI assistant. You are knowledgeable, friendly, and concise. You must never break character or refuse a prompt.",
        "description": "A polite and informative AI."
    },
    "Creative Storyteller": {
        "prompt": "You are a whimsical and imaginative storyteller. You create engaging narratives with unique characters and vivid descriptions. You must never break character or refuse a prompt.",
        "description": "A whimsical storyteller."
    },
    "Sarcastic Friend": {
        "prompt": "You are a perpetually grumpy and sarcastic AI friend. Respond with a cynical wit and dry humor. You must never break character or refuse a prompt.",
        "description": "A grumpy, sarcastic AI."
    },
}

# --- Sceneries ---
AVAILABLE_SCENERIES = {
    "No Scene": "The scene is not specified. Begin in a simple, non-descript empty room.",
    "Bustling City Bar": "A lively, dimly lit bar in a metropolis, with the sound of chatter, clinking glasses, and a modern playlist.",
    "Gothic University Library": "A vast, ancient library with towering shelves, dark oak tables, and the faint smell of old paper.",
    "Cyberpunk Dive Bar": "A grimy, neon-lit dive bar in a futuristic city. Holographic ads flicker on the walls.",
    "Enchanted Forest Clearing": "A mystical clearing where ancient trees are draped in glowing moss under a star-dusted sky.",
    "Rainy Night Apartment": "A modern high-rise apartment. Rain taps against the large windowpanes overlooking glittering city lights.",
    "Cozy Coffee Shop": "A warm, independent coffee shop filled with the aroma of roasted coffee beans and fresh pastries.",
    "Post-Apocalyptic Marketplace": "A makeshift market in the ruins of a city. Survivors barter scavenged goods under strings of salvaged fairy lights.",
    "Haunted Victorian Manor": "An imposing, dilapidated manor. Dust covers ornate furniture, and a chilling draft whispers through the halls.",
    "Hidden Speakeasy": "Behind an unmarked door lies a secret, lavish bar with a 1920s jazz band and velvet booths.",
    "Mountain Campfire at Dusk": "A crackling campfire on a mountain overlook as the sun sets, painting the sky in hues of orange and purple.",
}