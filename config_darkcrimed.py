# ============================================================
#  config_darkcrimed.py  —  Settings for the Dark Crime Decoded channel
# ============================================================
import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_KEY")
GEMINI_API_KEY        = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_KEY")
GROQ_API_KEY          = os.getenv("GROQ_API_KEY", "YOUR_GROQ_KEY")
ELEVENLABS_API_KEY    = os.getenv("ELEVENLABS_API_KEY", "YOUR_ELEVENLABS_KEY")
ELEVENLABS_VOICE_ID   = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
PEXELS_API_KEY        = os.getenv("PEXELS_API_KEY", "YOUR_PEXELS_KEY")
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID_DARKCRIMED", os.getenv("YOUTUBE_CLIENT_ID", "YOUR_YT_CLIENT_ID"))
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET_DARKCRIMED", os.getenv("YOUTUBE_CLIENT_SECRET", "YOUR_YT_SECRET"))

# TikTok
TIKTOK_SESSION_ID    = os.getenv("TIKTOK_SESSION_ID", "YOUR_TIKTOK_SESSION")
TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "YOUR_TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "YOUR_TIKTOK_CLIENT_SECRET")

# Instagram (Graph API)
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "YOUR_INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_BUSINESS_ID  = os.getenv("INSTAGRAM_BUSINESS_ID", "YOUR_INSTAGRAM_BUSINESS_ID")

# Facebook (Graph API)
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN", "YOUR_FACEBOOK_ACCESS_TOKEN")
FACEBOOK_PAGE_ID      = os.getenv("FACEBOOK_PAGE_ID", "YOUR_FACEBOOK_PAGE_ID")

# X / Twitter
X_API_KEY             = os.getenv("X_API_KEY", "YOUR_X_API_KEY")
X_API_SECRET          = os.getenv("X_API_SECRET", "YOUR_X_API_SECRET")
X_ACCESS_TOKEN        = os.getenv("X_ACCESS_TOKEN", "YOUR_X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET", "YOUR_X_ACCESS_TOKEN_SECRET")
X_BEARER_TOKEN        = os.getenv("X_BEARER_TOKEN", "YOUR_X_BEARER_TOKEN")

# --- Channel Identity ---
CHANNEL = "Dark Crime Decoded"

# --- Content Settings ---
NICHES = [
    "True crime — real story behind Breaking Bad",
    "True crime — real story behind Narcos",
    "True crime — real story behind Money Heist",
    "True crime — real story behind Peaky Blinders",
    "True crime — real story behind Ozark",
    "True crime — real story behind The Wire",
    "True crime — real story behind Griselda",
    "True crime — real story behind The Punisher",
    "True crime — real story behind The Godfather",
    "True crime — real story behind Goodfellas",
    "True crime — real story behind American Crime Story",
    "True crime — criminal psychology behind famous crime series",
]

NICHE_WEIGHTS = [0.15, 0.15, 0.10, 0.10, 0.08, 0.08, 0.08, 0.07, 0.07, 0.05, 0.05, 0.02]

VIDEOS_PER_DAY = 2
VIDEO_DURATION_SECONDS = 720       # 12 minutes
VIDEO_WIDTH  = 1080
VIDEO_HEIGHT = 1920

# --- Paths ---
OUTPUT_DIR  = "output/dark_crime"
AUDIO_DIR   = "output/dark_crime/audio"
VIDEO_DIR   = "output/dark_crime/video"
FINAL_DIR   = "output/dark_crime/final"
CONTENT_DIR = "content/dark_crime"

# YouTube token file for this channel
YOUTUBE_TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE_DARKCRIMED", "youtube_token_darkcrimed.json")

# --- Schedule ---
SCHEDULE_HOUR   = 7
SCHEDULE_MINUTE = 0
