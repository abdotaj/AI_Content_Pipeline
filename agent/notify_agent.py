# ============================================================
#  agents/notify_agent.py  —  Telegram notifications
# ============================================================
import requests
import time
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_message(text: str) -> dict:
    """Send a plain text message."""
    r = requests.post(f"{BASE_URL}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    })
    return r.json()


def send_video_preview(video_path: str, script_data: dict, video_id: str) -> str:
    """Send video info + approve/skip buttons as text message."""
    caption = (
        f"*Video Ready for Approval*\n\n"
        f"*Title:* {script_data['title']}\n"
        f"*Niche:* {script_data['niche']}\n"
        f"*Language:* {script_data.get('language', 'english')}\n"
        f"*Topic:* {script_data['topic']}\n\n"
        f"*Caption:*\n{script_data['caption']}\n\n"
        f"{script_data['hashtags']}"
    )

    keyboard = {
        "inline_keyboard": [[
            {"text": "Approve & Post", "callback_data": f"approve_{video_id}"},
            {"text": "Skip", "callback_data": f"skip_{video_id}"}
        ]]
    }

    r = requests.post(f"{BASE_URL}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": caption,
        "parse_mode": "Markdown",
        "reply_markup": keyboard
    })

    if not r.ok or not r.json().get("ok"):
        print(f"[Notify] Failed to send message: {r.text}")
        return "approve"  # auto-approve if notify fails

    print(f"[Notify] Message sent for {video_id}")
    return wait_for_decision(video_id)


def wait_for_decision(video_id: str, timeout: int = 300) -> str:
    """Poll for button tap. Times out after 5 minutes → auto-approve."""
    print(f"[Notify] Waiting for decision on {video_id}...")
    offset = None
    elapsed = 0
    poll_interval = 5

    while elapsed < timeout:
        params = {"timeout": poll_interval, "allowed_updates": ["callback_query"]}
        if offset:
            params["offset"] = offset

        try:
            r = requests.get(
                f"{BASE_URL}/getUpdates",
                params=params,
                timeout=poll_interval + 5
            )
            updates = r.json().get("result", [])
        except Exception:
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue

        for update in updates:
            offset = update["update_id"] + 1
            cb = update.get("callback_query")
            if cb:
                data = cb.get("data", "")
                requests.post(f"{BASE_URL}/answerCallbackQuery", json={
                    "callback_query_id": cb["id"],
                    "text": "Got it!"
                })
                if data == f"approve_{video_id}":
                    send_message(f"Approved! Posting *{video_id}*...")
                    return "approve"
                elif data == f"skip_{video_id}":
                    send_message(f"Skipped *{video_id}*.")
                    return "skip"

        elapsed += poll_interval

    print(f"[Notify] Timeout — auto-approving {video_id}")
    return "approve"


def send_daily_report(stats: dict) -> None:
    msg = (
        f"*Daily Report*\n\n"
        f"Generated: {stats.get('generated', 0)}\n"
        f"Posted: {stats.get('posted', 0)}\n"
        f"Skipped: {stats.get('skipped', 0)}\n"
        f"Errors: {stats.get('errors', 0)}"
    )
    send_message(msg)


def send_weekly_goal_report(analytics: dict) -> None:
    msg = (
        f"*Weekly Goal Report*\n\n"
        f"TikTok followers: {analytics.get('tiktok_followers', 'N/A')}\n"
        f"YouTube subscribers: {analytics.get('youtube_subs', 'N/A')}\n"
        f"Total views: {analytics.get('weekly_views', 'N/A')}\n"
        f"Est. revenue: ${analytics.get('est_revenue', '0')}"
    )
    send_message(msg)
