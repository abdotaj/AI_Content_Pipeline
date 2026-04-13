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
            size_kb = os.path.getsize(short_path) // 1024
            print(f"[Notify] Sending short clip: {short_path} ({size_kb}KB)")
            if size_kb < 10:
                print(f"[Notify] WARNING: short clip too small ({size_kb}KB) — skipping send")
            else:
                short_caption = f"SHORT VERSION — post this to TikTok, Instagram Reels and YouTube Shorts\n\n{hashtags}"
                with open(short_path, "rb") as sf:
                    requests.post(
                        f"{BASE_URL}/sendVideo",
                        data={
                            "chat_id": TELEGRAM_CHAT_ID,
                            "caption": short_caption[:1024],
                            "supports_streaming": "true",
                            "width": 1080,
                            "height": 1920,
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


def send_for_manual_posting(video_path: str, script_data: dict, platforms: str) -> None:
    """Send a video to Telegram for manual posting — no approval buttons."""
    title    = clean_text(script_data.get("title", ""))
    hashtags = script_data.get("hashtags", "")

    caption_text = (
        f"MANUAL POST NEEDED\n\n"
        f"Title: {title}\n"
        f"Post to: {platforms}\n\n"
        f"{hashtags}"
    )

    try:
        import os
        size_kb = os.path.getsize(video_path) // 1024
        print(f"[Notify] Sending video: {video_path} ({size_kb}KB)")
        if size_kb < 10:
            print(f"[Notify] WARNING: video file too small ({size_kb}KB) — may be corrupt")
        with open(video_path, "rb") as video_file:
            r = requests.post(
                f"{BASE_URL}/sendVideo",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption_text[:1024],
                    "supports_streaming": "true",
                    "width": 1080,
                    "height": 1920,
                },
                files={"video": video_file}
            )
        if r.ok:
            print(f"[Notify] Clip sent to Telegram for manual posting ({platforms})")
        else:
            print(f"[Notify] Failed to send for manual posting: {r.text}")
    except Exception as e:
        print(f"[Notify] Manual posting notification failed: {e}")


def _add_section_headers(script: str, intro_label: str, main_label: str, conclusion_label: str) -> str:
    """Divide a continuous script into three labelled sections."""
    paragraphs = [p.strip() for p in script.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [script]
    n = len(paragraphs)
    intro_end        = max(1, n // 5)
    conclusion_start = max(intro_end + 1, (n * 4) // 5)
    intro      = "\n\n".join(paragraphs[:intro_end])
    main       = "\n\n".join(paragraphs[intro_end:conclusion_start])
    conclusion = "\n\n".join(paragraphs[conclusion_start:])
    parts = []
    if intro:
        parts.append(f"{intro_label}:\n{intro}")
    if main:
        parts.append(f"{main_label}:\n{main}")
    if conclusion:
        parts.append(f"{conclusion_label}:\n{conclusion}")
    return "\n─────────────────\n".join(parts)


def _send_long_text(text: str, max_len: int = 3900) -> None:
    """Send text to Telegram, splitting at paragraph breaks if too long."""
    text = text.strip()
    while text:
        if len(text) <= max_len:
            send_message(text)
            break
        chunk = text[:max_len]
        split = chunk.rfind("\n\n")
        if split < max_len // 2:
            split = chunk.rfind("\n")
        if split < max_len // 2:
            split = max_len
        send_message(text[:split].rstrip())
        text = text[split:].lstrip()


def send_script_preview(en_script: dict, ar_script: dict | None = None) -> None:
    """Send English script (and optionally Arabic) to Telegram."""
    send_english_script_preview(en_script)
    if ar_script:
        send_arabic_script_preview(ar_script)


def send_english_script_preview(script: dict, label: str = "ENGLISH SCRIPT") -> None:
    """Send English script to Telegram. `label` appears as the message header."""
    body = _add_section_headers(
        script.get("script", ""),
        intro_label="INTRO", main_label="MAIN STORY", conclusion_label="CONCLUSION",
    )
    msg = (
        f"{label}\n\n"
        f"Title: {script.get('title', '')}\n"
        f"─────────────────\n"
        f"{body}\n"
        f"─────────────────\n"
        f"Send voice message to use your voice.\n"
        f"Or wait — AI voice used automatically in 60 minutes."
    )
    _send_long_text(msg)
    print(f"[Notify] English script sent to Telegram ({label})")


def send_arabic_script_preview(script: dict, label: str = "النص العربي") -> None:
    """Send Arabic script to Telegram. `label` appears as the message header."""
    body = _add_section_headers(
        script.get("script", ""),
        intro_label="مقدمة", main_label="القصة الرئيسية", conclusion_label="الخاتمة",
    )
    msg = (
        f"{label}\n\n"
        f"العنوان: {script.get('title', '')}\n"
        f"─────────────────\n"
        f"{body}\n"
        f"─────────────────\n"
        f"أرسل رسالة صوتية لاستخدام صوتك.\n"
        f"أو انتظر — سيتم استخدام الصوت الآلي تلقائياً خلال 60 دقيقة."
    )
    _send_long_text(msg)
    print(f"[Notify] Arabic script sent to Telegram ({label})")


def listen_for_voice_message(language: str, timeout: int = 600) -> str:
    """
    Poll Telegram for a voice message up to `timeout` seconds.
    Returns the local path to the downloaded audio file, or "" if none received.
    """
    import os
    from pathlib import Path

    audio_dir = Path("output/voice_messages")
    audio_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Notify] Waiting for {language} voice message ({timeout}s)...")
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
            voice = msg.get("voice") or msg.get("audio")
            if not voice:
                continue
            file_id = voice.get("file_id", "")
            if not file_id:
                continue
            try:
                file_r = requests.get(f"{BASE_URL}/getFile", params={"file_id": file_id}, timeout=10)
                file_path = file_r.json().get("result", {}).get("file_path", "")
                if not file_path:
                    continue
                dl_r = requests.get(
                    f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
                    timeout=30
                )
                ext = os.path.splitext(file_path)[1] or ".ogg"
                out_path = audio_dir / f"voice_{language}_{update['update_id']}{ext}"
                out_path.write_bytes(dl_r.content)
                print(f"[Notify] {language} voice received: {out_path}")
                send_message(f"Voice message received! Using your voice for the {language} video.")
                return str(out_path)
            except Exception as e:
                print(f"[Notify] Voice download failed: {e}")

        elapsed += poll_interval

    print(f"[Notify] No {language} voice message received — using AI voice")
    return ""


def check_telegram_for_script(timeout: int = 15) -> dict | None:
    """
    Check Telegram for messages sent in the last 10 minutes from the owner chat.
    Marks ALL pending updates as read regardless of outcome.

    Returns:
      {"type": "topic", "content": <text>}  — short message treated as topic name
      None                                   — nothing recent found
    """
    import time

    current_time = time.time()

    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params={"limit": 100}, timeout=20)
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"[Notify] check_telegram_for_script failed: {e}")
        return None

    if not updates:
        return None

    # Always mark all pending messages as read
    last_update_id = updates[-1]["update_id"]
    try:
        requests.get(f"{BASE_URL}/getUpdates", params={"offset": last_update_id + 1}, timeout=10)
    except Exception:
        pass

    topic_found = None

    for update in reversed(updates):  # newest first
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        msg_time = message.get("date", 0)

        # Only accept messages from the owner chat
        if chat_id != str(TELEGRAM_CHAT_ID):
            continue

        # Only accept messages from the last 10 minutes
        if current_time - msg_time > 600:
            continue

        # Ignore commands and empty messages
        if not text or text.startswith("/") or text.startswith("[") or text.startswith("*"):
            continue

        # Short topic name (2–100 chars)
        if 2 < len(text) < 100:
            print(f"[Notify] Topic from Telegram: {text!r}")
            topic_found = {"type": "topic", "content": text}
            break

    return topic_found


def send_daily_report(stats: dict) -> None:
    msg = (
        f"Daily Report\n\n"
        f"Generated: {stats.get('generated', 0)}\n"
        f"Posted: {stats.get('posted', 0)}\n"
        f"Skipped: {stats.get('skipped', 0)}\n"
        f"Errors: {stats.get('errors', 0)}"
    )
    send_message(msg)
