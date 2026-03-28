# ============================================================
#  config.py  —  All API keys and settings in one place
# ============================================================
import os

# --- API Keys (set these as environment variables or paste here) ---
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_KEY")
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_KEY")
GROQ_API_KEY         = os.getenv("GROQ_API_KEY", "YOUR_GROQ_KEY")
ELEVENLABS_API_KEY   = os.getenv("ELEVENLABS_API_KEY", "YOUR_ELEVENLABS_KEY")
ELEVENLABS_VOICE_ID  = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel voice
PEXELS_API_KEY       = os.getenv("PEXELS_API_KEY", "YOUR_PEXELS_KEY")
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
YOUTUBE_CLIENT_ID    = os.getenv("YOUTUBE_CLIENT_ID", "YOUR_YT_CLIENT_ID")
YOUTUBE_CLIENT_SECRET= os.getenv("YOUTUBE_CLIENT_SECRET", "YOUR_YT_SECRET")

# TikTok (uses unofficial approach — see README)
TIKTOK_SESSION_ID    = os.getenv("TIKTOK_SESSION_ID", "YOUR_TIKTOK_SESSION")

# Instagram (Graph API)
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "YOUR_INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_BUSINESS_ID  = os.getenv("INSTAGRAM_BUSINESS_ID", "YOUR_INSTAGRAM_BUSINESS_ID")

# Facebook (Graph API)
FACEBOOK_ACCESS_TOKEN  = os.getenv("FACEBOOK_ACCESS_TOKEN", "YOUR_FACEBOOK_ACCESS_TOKEN")
FACEBOOK_PAGE_ID       = os.getenv("FACEBOOK_PAGE_ID", "YOUR_FACEBOOK_PAGE_ID")

# --- Content Settings ---
NICHES = ["AI & Tech news", "Educational facts", "Motivation & mindset"]
NICHE_WEIGHTS = [0.5, 0.3, 0.2]   # 50% AI, 30% facts, 20% motivation

VIDEOS_PER_DAY = 2                 # how many videos to generate daily
VIDEO_DURATION_SECONDS = 45        # target video length (30-60 recommended)
VIDEO_WIDTH  = 1080
VIDEO_HEIGHT = 1920                # vertical 9:16 for TikTok/Shorts

# --- Paths ---
OUTPUT_DIR    = "output"
AUDIO_DIR     = "output/audio"
VIDEO_DIR     = "output/video"
FINAL_DIR     = "output/final"

# --- Schedule ---
# Run daily at 7:00 AM (set this in your cron or GitHub Actions)
SCHEDULE_HOUR   = 7
SCHEDULE_MINUTE = 0

# --- X / Twitter API Keys ---
X_API_KEY            = os.getenv("X_API_KEY", "YOUR_X_API_KEY")
X_API_SECRET         = os.getenv("X_API_SECRET", "YOUR_X_API_SECRET")
X_ACCESS_TOKEN       = os.getenv("X_ACCESS_TOKEN", "YOUR_X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET= os.getenv("X_ACCESS_TOKEN_SECRET", "YOUR_X_ACCESS_TOKEN_SECRET")
X_BEARER_TOKEN       = os.getenv("X_BEARER_TOKEN", "YOUR_X_BEARER_TOKEN")
