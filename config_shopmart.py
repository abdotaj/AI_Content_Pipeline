# ============================================================
#  config_shopmart.py  —  Settings for the Shopmart Global channel
# ============================================================
import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_KEY")
GEMINI_API_KEY        = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_KEY")
GROQ_API_KEY          = os.getenv("GROQ_API_KEY", "YOUR_GROQ_KEY")
ELEVENLABS_API_KEY    = os.getenv("ELEVENLABS_API_KEY", "YOUR_ELEVENLABS_KEY")
ELEVENLABS_VOICE_ID   = os.getenv("ELEVENLABS_VOICE_ID_SHOPMART", os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
PEXELS_API_KEY        = os.getenv("PEXELS_API_KEY", "YOUR_PEXELS_KEY")
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID_SHOPMART", os.getenv("YOUTUBE_CLIENT_ID", "YOUR_YT_CLIENT_ID"))
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET_SHOPMART", os.getenv("YOUTUBE_CLIENT_SECRET", "YOUR_YT_SECRET"))

# TikTok
TIKTOK_SESSION_ID    = os.getenv("TIKTOK_SESSION_ID_SHOPMART", os.getenv("TIKTOK_SESSION_ID", "YOUR_TIKTOK_SESSION"))
TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "YOUR_TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "YOUR_TIKTOK_CLIENT_SECRET")

# Instagram (Graph API)
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN_SHOPMART", os.getenv("INSTAGRAM_ACCESS_TOKEN", "YOUR_INSTAGRAM_ACCESS_TOKEN"))
INSTAGRAM_BUSINESS_ID  = os.getenv("INSTAGRAM_BUSINESS_ID_SHOPMART", os.getenv("INSTAGRAM_BUSINESS_ID", "YOUR_INSTAGRAM_BUSINESS_ID"))

# Facebook (Graph API)
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN_SHOPMART", os.getenv("FACEBOOK_ACCESS_TOKEN", "YOUR_FACEBOOK_ACCESS_TOKEN"))
FACEBOOK_PAGE_ID      = os.getenv("FACEBOOK_PAGE_ID_SHOPMART", os.getenv("FACEBOOK_PAGE_ID", "YOUR_FACEBOOK_PAGE_ID"))

# X / Twitter
X_API_KEY             = os.getenv("X_API_KEY", "YOUR_X_API_KEY")
X_API_SECRET          = os.getenv("X_API_SECRET", "YOUR_X_API_SECRET")
X_ACCESS_TOKEN        = os.getenv("X_ACCESS_TOKEN", "YOUR_X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET", "YOUR_X_ACCESS_TOKEN_SECRET")
X_BEARER_TOKEN        = os.getenv("X_BEARER_TOKEN", "YOUR_X_BEARER_TOKEN")

# --- Channel Identity ---
CHANNEL = "Shopmart Global"

# --- Content Settings ---
NICHES = [
    "ecommerce — best online shopping deals and product reviews",
    "online shopping — top gadgets and electronics under budget",
    "product reviews — best projectors and home theatre gadgets",
    "ecommerce — skincare and beauty product recommendations",
    "online shopping — toys and collectibles for kids",
    "home decor — best lighting and interior products online",
    "product reviews — top kitchen and household gadgets",
    "ecommerce — exclusive deals and flash sales",
]

NICHE_WEIGHTS = [0.20, 0.15, 0.15, 0.12, 0.12, 0.10, 0.08, 0.08]

VIDEOS_PER_DAY = 2
VIDEO_DURATION_SECONDS = 90        # Short-form product videos
VIDEO_WIDTH  = 1080
VIDEO_HEIGHT = 1920

# --- Paths ---
OUTPUT_DIR  = "output/shopmart"
AUDIO_DIR   = "output/shopmart/audio"
VIDEO_DIR   = "output/shopmart/video"
FINAL_DIR   = "output/shopmart/final"
CONTENT_DIR = "content/shopmart"

# YouTube token file for this channel
YOUTUBE_TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE_SHOPMART", "youtube_token_shopmart.json")

# --- Schedule ---
SCHEDULE_HOUR   = 9
SCHEDULE_MINUTE = 0
