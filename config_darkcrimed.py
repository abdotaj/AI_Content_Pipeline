# ============================================================
#  config_darkcrimed.py  —  Settings for the Dark Crime Decoded channel
# ============================================================
import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_KEY")
OPENAI_API_KEY        = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY        = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_KEY")
GROQ_API_KEY          = os.getenv("GROQ_API_KEY", "YOUR_GROQ_KEY")
ELEVENLABS_API_KEY    = os.getenv("ELEVENLABS_API_KEY", "YOUR_ELEVENLABS_KEY")
ELEVENLABS_VOICE_ID   = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_VOICE_ID_EN = os.getenv("ELEVENLABS_VOICE_ID_EN", "oHXsMWwdWLsNE9IdmbuT")
ELEVENLABS_VOICE_ID_AR = os.getenv("ELEVENLABS_VOICE_ID_AR", "kVE76Ng0Z4kGR7oebETP")
PEXELS_API_KEY        = os.getenv("PEXELS_API_KEY", "YOUR_PEXELS_KEY")
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID_DARKCRIMED", os.getenv("YOUTUBE_CLIENT_ID", "YOUR_YT_CLIENT_ID"))
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET_DARKCRIMED", os.getenv("YOUTUBE_CLIENT_SECRET", "YOUR_YT_SECRET"))

# Per-language channel credentials
YOUTUBE_CLIENT_ID_EN     = os.getenv("YOUTUBE_CLIENT_ID_EN", "")
YOUTUBE_CLIENT_SECRET_EN = os.getenv("YOUTUBE_CLIENT_SECRET_EN", "")
YOUTUBE_CLIENT_ID_AR     = os.getenv("YOUTUBE_CLIENT_ID_AR", "")
YOUTUBE_CLIENT_SECRET_AR = os.getenv("YOUTUBE_CLIENT_SECRET_AR", "")
YOUTUBE_TOKEN_FILE_EN    = "youtube_token_darkcrimed_en.json"
YOUTUBE_TOKEN_FILE_AR    = "youtube_token_darkcrimed_ar.json"

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
    "Real story behind The Godfather movie",
    "Real story behind Scarface movie",
    "Real story behind Narcos Netflix series",
    "Real story behind Money Heist Netflix series",
    "Real story behind Breaking Bad series",
    "Real story behind Peaky Blinders series",
    "Real story behind Goodfellas movie",
    "Real story behind Casino movie",
    "Real story behind Ozark Netflix series",
    "Real story behind The Wire series",
    "Real story behind Griselda Netflix series",
    "Real story behind American Gangster movie",
    "Real story behind Donnie Brasco movie",
    "Real story behind City of God movie",
    "Real story behind Sicario movie",
]

NICHE_WEIGHTS = [0.10, 0.10, 0.10, 0.08, 0.08,
                 0.08, 0.08, 0.07, 0.07, 0.07,
                 0.06, 0.05, 0.04, 0.01, 0.01]

VIDEOS_PER_DAY = 1
LONG_VIDEO_DURATION  = 660   # 11 minutes target (10-12 min = 600-720 s); audio drives actual length
SHORT_VIDEO_DURATION = 75    # 75 seconds target (60-90 s range); audio drives actual length
EDGETTS_RATE         = "+0%"   # edge-tts speaking rate for fallback TTS (+0% = normal speed)
# Legacy aliases — kept for backward compatibility
VIDEO_DURATION_SECONDS = LONG_VIDEO_DURATION
SHORT_CLIP_DURATION    = SHORT_VIDEO_DURATION
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
