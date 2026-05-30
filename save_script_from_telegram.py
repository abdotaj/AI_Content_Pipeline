#!/usr/bin/env python3
"""
Save a script sent as a .txt document to the Telegram bot.

Requires: .env file with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID set.

Usage:
    python save_script_from_telegram.py

Workflow:
    1. Prepare a .txt file in the bilingual injection format (see below)
    2. Send it to @AAmycontentbot_bot on Telegram
    3. Run this script — it downloads and saves to content/scripts/
    4. The next pipeline run will auto-use it (skipping Groq + translation)

Expected .txt format:
    TITLE: The Story Title | Dark Crime Decoded
    TITLE_AR: عنوان القصة | Dark Crime Decoded
    TOPIC: Person or Event Name
    SERIES: Series Name (or leave blank)
    ---
    [SECTION: Introduction]
    English script here...

    [SECTION: The Rise]
    More English...
    ===
    [SECTION: المقدمة]
    النص العربي هنا...

    [SECTION: الصعود]
    المزيد من النص...
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.notify_agent import save_script_from_telegram

if __name__ == "__main__":
    result = save_script_from_telegram(max_age_hours=24)
    if result:
        print(f"\nDone. Script saved to: {result}")
        print("Run the pipeline now — it will use this script automatically.")
    else:
        print("\nNo script document found.")
        print("Send a .txt file to the Telegram bot (@AAmycontentbot_bot) and try again.")
