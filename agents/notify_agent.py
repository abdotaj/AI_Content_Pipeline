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
    discovery_block = ""
    user_discovery = script.get("user_discovery", "")
    discovery_expanded = script.get("user_discovery_expanded", [])
    if user_discovery:
        expanded_lines = "\n".join(f"  - {d}" for d in (discovery_expanded or []))
        discovery_block = (
            f"─────────────────\n"
            f"YOUR DISCOVERY:\n{user_discovery}\n\n"
            f"WHAT WE FOUND:\n{expanded_lines or '(see script above)'}\n"
        )
    msg = (
        f"{label}\n\n"
        f"Title: {script.get('title', '')}\n"
        f"─────────────────\n"
        f"{discovery_block}"
        f"{body}\n"
        f"─────────────────\n"
        f"Generating video automatically..."
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
        f"جاري إنشاء الفيديو تلقائياً..."
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


_SYSTEM_SKIP_WORDS = [
    # pipeline status messages sent by the bot itself
    "approve", "reject", "skip",
    "pipeline starting", "daily report",
    "generated", "posted", "errors",
    "script sent", "video ready",
    "youtube", "http", "telegram",
    "upload failed", "upload to",
    "dark crime decoded —",
    # emoji markers used in bot messages
    "✅", "❌", "📋", "📹", "📱", "🎬", "━",
]


def check_telegram_for_script(timeout: int = 15) -> dict | None:
    """
    Check Telegram for a user-sent topic in the last 10 minutes.

    Marks ALL pending updates as read first, then scans newest-first for
    a short human topic string — skips any message that looks like a bot
    status report (pipeline updates, YouTube URLs, emoji markers, etc.).

    Returns {"type": "research_note", "content": text, "is_detailed": bool}
    or None if nothing fresh found.
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

    # Mark ALL as read FIRST — prevents stale messages from leaking into next run
    last_update_id = updates[-1]["update_id"]
    try:
        requests.get(f"{BASE_URL}/getUpdates", params={"offset": last_update_id + 1}, timeout=10)
    except Exception:
        pass

    for update in reversed(updates):  # newest first
        message = update.get("message", {})
        text     = message.get("text", "").strip()
        chat_id  = str(message.get("chat", {}).get("id", ""))
        msg_time = message.get("date", 0)

        # Only owner chat
        if chat_id != str(TELEGRAM_CHAT_ID):
            continue

        # Only last 10 minutes
        if current_time - msg_time > 600:
            continue

        # Skip bot commands and empty text
        if not text or text.startswith("/") or text.startswith("[") or text.startswith("*"):
            continue

        # Skip messages that look like bot status reports
        text_lower = text.lower()
        if any(word in text_lower for word in _SYSTEM_SKIP_WORDS):
            print(f"[Notify] Skipping system message: {text[:60]!r}")
            continue

        # Must be a plausible topic length
        if 2 < len(text) < 200:
            is_detailed = len(text) > 50
            print(f"[Notify] Topic from Telegram ({'detailed' if is_detailed else 'topic'}): {text[:80]!r}")
            return {"type": "research_note", "content": text, "is_detailed": is_detailed}

    return None


def download_telegram_photo(file_id: str, caption: str | None = None) -> dict | None:
    """
    Download a photo from Telegram, resize to 1080x1920, and extract tags from caption.

    Returns {"path": local_path, "tags": [...], "caption": ...} or None on failure.
    Tags are words from the caption (>3 chars) used to match images to script sections.
    """
    from pathlib import Path as _Path

    # Get file path
    try:
        r = requests.get(f"{BASE_URL}/getFile", params={"file_id": file_id}, timeout=10)
        info = r.json()
        if not info.get("ok"):
            return None
        file_path = info["result"]["file_path"]
    except Exception as e:
        print(f"[Notify] getFile failed: {e}")
        return None

    # Download raw bytes
    try:
        dl_r = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
            timeout=60,
        )
        if dl_r.status_code != 200:
            return None
    except Exception as e:
        print(f"[Notify] Photo download failed: {e}")
        return None

    # Save + resize to 1080x1920
    _Path("output/user_images").mkdir(parents=True, exist_ok=True)
    local_path = f"output/user_images/user_{int(time.time())}_{file_id[:8]}.jpg"
    with open(local_path, "wb") as f:
        f.write(dl_r.content)

    try:
        from PIL import Image as PILImage
        img = PILImage.open(local_path).convert("RGB")
        img = img.resize((1080, 1920), PILImage.LANCZOS)
        img.save(local_path)
    except Exception as e:
        print(f"[Notify] Resize failed for {local_path}: {e}")

    # Extract tags from caption (words >3 chars, lowercased)
    tags: list[str] = []
    if caption:
        tags = [w.lower() for w in caption.split() if len(w) > 3]

    return {"path": local_path, "tags": tags, "caption": caption or ""}


def check_telegram_for_images() -> list[dict]:
    """
    Check for photos sent to the bot in the last 10 minutes from the owner chat.
    Does NOT mark updates as read — call this BEFORE check_telegram_for_script().

    Returns list of {"path": ..., "tags": [...], "caption": ...} dicts,
    newest-first (so the most recent photo is at index 0).
    """
    current_time = time.time()

    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params={"limit": 100}, timeout=20)
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"[Notify] check_telegram_for_images failed: {e}")
        return []

    user_images: list[dict] = []

    for update in reversed(updates):  # newest first
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        msg_time = message.get("date", 0)
        caption = message.get("caption", "") or ""

        if chat_id != str(TELEGRAM_CHAT_ID):
            continue
        if current_time - msg_time > 600:
            continue

        photos = message.get("photo", [])
        if not photos:
            continue

        # Use highest quality photo
        best = max(photos, key=lambda p: p.get("file_size", 0))
        img_info = download_telegram_photo(best["file_id"], caption=caption)
        if img_info:
            user_images.append(img_info)
            print(f"[Notify] User image downloaded: {img_info['path']} (tags: {img_info['tags'][:4]})")

    return user_images


def send_video_to_telegram(video_path: str, caption: str, label: str) -> dict:
    """Send a video to Telegram. Uses sendVideo under 50 MB, sendDocument above."""
    import os
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"[Notify] Sending {label}: {file_size_mb:.1f}MB")

    if file_size_mb > 50:
        print(f"[Notify] File too large for sendVideo ({file_size_mb:.1f}MB) — sending as document")
        url       = f"{BASE_URL}/sendDocument"
        files_key = "document"
    else:
        url       = f"{BASE_URL}/sendVideo"
        files_key = "video"

    with open(video_path, "rb") as f:
        response = requests.post(
            url,
            data={
                "chat_id":           TELEGRAM_CHAT_ID,
                "caption":           caption[:1024],
                "supports_streaming": True,
                "width":             1080,
                "height":            1920,
            },
            files={files_key: f},
            timeout=300,
        )

    result = response.json()
    if result.get("ok"):
        print(f"[Notify] {label} sent successfully")
    else:
        print(f"[Notify] {label} failed: {result.get('description')}")
    return result


def send_daily_report(stats: dict) -> None:
    msg = (
        f"Daily Report\n\n"
        f"Generated: {stats.get('generated', 0)}\n"
        f"Posted: {stats.get('posted', 0)}\n"
        f"Skipped: {stats.get('skipped', 0)}\n"
        f"Errors: {stats.get('errors', 0)}"
    )
    send_message(msg)
