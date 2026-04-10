# ============================================================
#  agents/notify_agent.py  —  Telegram notifications
# ============================================================
import json
import requests
import time
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def clean_text(text: str) -> str:
    """Remove special markdown characters that break Telegram."""
    for char in ['*', '_', '`', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(char, ' ')
    return text.strip()


def send_message(text: str) -> dict:
    """Send a plain text message — no markdown."""
    r = requests.post(f"{BASE_URL}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    })
    return r.json()


def send_video_preview(video_path: str, script_data: dict, video_id: str) -> str:
    """Send video info + approve/skip buttons."""
    title = clean_text(script_data.get('title', ''))
    niche = clean_text(script_data.get('niche', ''))
    topic = clean_text(script_data.get('topic', ''))
    language = script_data.get('language', 'english')
    caption = clean_text(script_data.get('caption', ''))
    hashtags = script_data.get('hashtags', '')

    caption_text = (
        f"Video Ready for Approval\n\n"
        f"Title: {title}\n"
        f"Niche: {niche}\n"
        f"Language: {language}\n"
        f"Topic: {topic}\n\n"
        f"Caption:\n{caption}\n\n"
        f"{hashtags}"
    )

    keyboard = {
        "inline_keyboard": [[
            {"text": "Approve & Post", "callback_data": f"approve_{video_id}"},
            {"text": "Skip", "callback_data": f"skip_{video_id}"}
        ]]
    }

    with open(video_path, "rb") as video_file:
        r = requests.post(
            f"{BASE_URL}/sendVideo",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption_text[:1024],
                "reply_markup": json.dumps(keyboard),
                "supports_streaming": "true",
            },
            files={"video": video_file}
        )

    if not r.ok or not r.json().get("ok"):
        print(f"[Notify] Failed: {r.text}")
        return "approve"

    print(f"[Notify] Video sent for {video_id}")

    # Send short clip if it was generated
    short_path = script_data.get("short_clip_path", "")
    if short_path:
        import os
        from pathlib import Path
        if Path(short_path).exists():
            short_caption = f"SHORT VERSION — post this to TikTok, Instagram Reels and YouTube Shorts\n\n{hashtags}"
            with open(short_path, "rb") as sf:
                requests.post(
                    f"{BASE_URL}/sendVideo",
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "caption": short_caption[:1024],
                        "supports_streaming": "true",
                    },
                    files={"video": sf}
                )
            print(f"[Notify] Short clip sent for {video_id}")

    return wait_for_decision(video_id)


def wait_for_decision(video_id: str, timeout: int = 300) -> str:
    """Poll for button tap. Times out after 5 min → auto-approve."""
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
                    send_message(f"Approved! Posting {video_id}...")
                    return "approve"
                elif data == f"skip_{video_id}":
                    send_message(f"Skipped {video_id}.")
                    return "skip"

        elapsed += poll_interval

    print(f"[Notify] Timeout — auto-approving {video_id}")
    return "approve"


def listen_for_content(timeout: int = 60) -> None:
    """Poll Telegram for text messages or .txt/.docx files, save to content/pending/."""
    import os
    from pathlib import Path

    pending_dir = Path("content/pending")
    pending_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Notify] Listening for content drops on Telegram ({timeout}s)...")
    offset = None
    elapsed = 0
    poll_interval = 5

    while elapsed < timeout:
        params = {"timeout": poll_interval, "allowed_updates": ["message"]}
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
            msg = update.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            if not chat_id:
                continue

            # Text message → use as script topic/content
            text = msg.get("text", "").strip()
            if text and not text.startswith("/"):
                filename = pending_dir / f"telegram_{update['update_id']}.txt"
                filename.write_text(text, encoding="utf-8")
                print(f"[Notify] Text content saved: {filename}")
                requests.post(f"{BASE_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "Content received! Will be used in next video."
                })
                continue

            # Document (.txt or .docx)
            doc = msg.get("document", {})
            doc_name = doc.get("file_name", "")
            if doc and doc_name.lower().endswith((".txt", ".docx")):
                file_id = doc["file_id"]
                file_r = requests.get(f"{BASE_URL}/getFile", params={"file_id": file_id})
                file_path = file_r.json().get("result", {}).get("file_path", "")
                if file_path:
                    dl_r = requests.get(
                        f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                    )
                    out_path = pending_dir / f"telegram_{update['update_id']}_{doc_name}"
                    out_path.write_bytes(dl_r.content)
                    print(f"[Notify] File content saved: {out_path}")
                    requests.post(f"{BASE_URL}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "Content received! Will be used in next video."
                    })

        elapsed += poll_interval


def send_daily_report(stats: dict) -> None:
    msg = (
        f"Daily Report\n\n"
        f"Generated: {stats.get('generated', 0)}\n"
        f"Posted: {stats.get('posted', 0)}\n"
        f"Skipped: {stats.get('skipped', 0)}\n"
        f"Errors: {stats.get('errors', 0)}"
    )
    send_message(msg)
