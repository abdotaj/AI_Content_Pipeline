# ============================================================
#  agents/notify_agent.py  —  Telegram bot with approve buttons
# ============================================================
import requests
import json
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
    """
    Send video preview + inline approve/skip buttons.
    Returns 'approve' or 'skip' based on your tap.
    """
    caption = (
        f"*{script_data['title']}*\n\n"
        f"Niche: {script_data['niche']}\n"
        f"Topic: {script_data['topic']}\n\n"
        f"Caption preview:\n_{script_data['caption']}_\n\n"
        f"{script_data['hashtags']}"
    )

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve & Post", "callback_data": f"approve_{video_id}"},
            {"text": "❌ Skip",           "callback_data": f"skip_{video_id}"}
        ]]
    }

    # Send video file with buttons
    with open(video_path, "rb") as video_file:
        r = requests.post(
            f"{BASE_URL}/sendVideo",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(keyboard),
                "supports_streaming": True
            },
            files={"video": video_file}
        )

    if not r.ok:
        print(f"[Notify] Failed to send video: {r.text}")
        # Fallback: send text only
        send_message(f"Video ready (could not send preview):\n{caption}")
        return wait_for_decision(video_id)

    print(f"[Notify] Preview sent for {video_id}")
    return wait_for_decision(video_id)


def wait_for_decision(video_id: str, timeout: int = 3600) -> str:
    """
    Poll Telegram for your button tap.
    Returns 'approve' or 'skip'. Times out after 1 hour → auto-approve.
    """
    print(f"[Notify] Waiting for your decision on {video_id}...")
    offset = None
    elapsed = 0
    poll_interval = 5

    while elapsed < timeout:
        params = {"timeout": poll_interval, "allowed_updates": ["callback_query"]}
        if offset:
            params["offset"] = offset

        try:
            r = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=poll_interval + 5)
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
                # Acknowledge the button tap
                requests.post(f"{BASE_URL}/answerCallbackQuery", json={
                    "callback_query_id": cb["id"],
                    "text": "Got it!"
                })
                if data == f"approve_{video_id}":
                    send_message(f"Approved! Posting *{video_id}* now...")
                    return "approve"
                elif data == f"skip_{video_id}":
                    send_message(f"Skipped *{video_id}*.")
                    return "skip"

        elapsed += poll_interval

    # Auto-approve after timeout
    send_message(f"No response in 1 hour — auto-approving *{video_id}*.")
    return "approve"


def send_daily_report(stats: dict) -> None:
    """Send a daily summary report."""
    msg = (
        f"*Daily Report*\n\n"
        f"Videos generated: {stats.get('generated', 0)}\n"
        f"Videos posted: {stats.get('posted', 0)}\n"
        f"Videos skipped: {stats.get('skipped', 0)}\n\n"
        f"Platforms: TikTok + YouTube\n"
        f"Next run: tomorrow at 7:00 AM"
    )
    send_message(msg)


def send_weekly_goal_report(analytics: dict) -> None:
    """Send a weekly goal progress report."""
    msg = (
        f"*Weekly Goal Report*\n\n"
        f"TikTok followers: {analytics.get('tiktok_followers', 'N/A')}\n"
        f"YouTube subscribers: {analytics.get('youtube_subs', 'N/A')}\n"
        f"Total views this week: {analytics.get('weekly_views', 'N/A')}\n"
        f"Est. monthly revenue: ${analytics.get('est_revenue', '0')}\n\n"
        f"Goal progress:\n"
        f"  To 1K: {analytics.get('to_1k', 'tracking...')}\n"
        f"  To monetization: {analytics.get('to_monetize', 'tracking...')}"
    )
    send_message(msg)
