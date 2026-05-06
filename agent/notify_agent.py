# ============================================================
#  agents/notify_agent.py  —  Telegram notifications
# ============================================================
import json
import os
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
    import re
    # If the script already has section markers, keep it as-is to avoid duplicated previews.
    if re.search(r'(?im)^\s*[\[\{\(]\s*(?:section|chapter|part|القسم|قسم)\s*:', script or ""):
        return (script or "").strip()

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


def check_telegram_for_script(timeout: int = 30) -> dict | None:
    """
    Check Telegram for a user-sent topic from the last 4 hours.

    Marks ALL pending updates as read first, then scans newest-first.
    Skips any message that looks like a bot status report.
    4-hour window so users can send a topic hours before the pipeline runs.

    Returns {"type": "research_note", "content": text, "is_detailed": bool}
    or None if nothing found.
    """
    import time

    current_time = time.time()
    MAX_AGE = 4 * 60 * 60  # 4 hours — user may send topic before pipeline starts

    print(f"[Notify] Checking Telegram messages (window: 4 hours)...")

    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params={"limit": 100}, timeout=20)
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"[Notify] check_telegram_for_script failed: {e}")
        return None

    print(f"[Notify] Found {len(updates)} pending updates")

    # Debug: show last 5 messages with age
    for upd in updates[-5:]:
        msg      = upd.get("message", {})
        msg_time = msg.get("date", 0)
        msg_text = msg.get("text", "")[:60]
        age      = current_time - msg_time
        print(f"[Notify] Message age: {age:.0f}s  text: {msg_text!r}")

    if not updates:
        print(f"[Notify] No pending updates")
        return None

    # Mark ALL as read FIRST — prevents leaking into next run
    last_update_id = updates[-1]["update_id"]
    try:
        requests.get(f"{BASE_URL}/getUpdates", params={"offset": last_update_id + 1}, timeout=10)
        print(f"[Notify] Marked {len(updates)} updates as read")
    except Exception:
        pass

    for update in reversed(updates):  # newest first
        message  = update.get("message", {})
        text     = message.get("text", "").strip()
        chat_id  = str(message.get("chat", {}).get("id", ""))
        msg_time = message.get("date", 0)
        age      = current_time - msg_time

        # Only owner chat
        if chat_id != str(TELEGRAM_CHAT_ID):
            continue

        # 4-hour window
        if age > MAX_AGE:
            print(f"[Notify] Message too old ({age/3600:.1f}h): {text[:40]!r}")
            continue

        # Skip bot commands and empty text
        if not text or text.startswith("/") or text.startswith("[") or text.startswith("*"):
            continue

        # Skip messages that look like bot status reports
        text_lower = text.lower()
        if any(word in text_lower for word in _SYSTEM_SKIP_WORDS):
            print(f"[Notify] Skipping system message ({age:.0f}s old): {text[:60]!r}")
            continue

        # Valid topic
        if 2 < len(text) < 200:
            is_detailed = len(text) > 50
            print(f"[Notify] FOUND topic ({age:.0f}s old): {text[:80]!r}")
            return {"type": "research_note", "content": text, "is_detailed": is_detailed}

    print(f"[Notify] No valid topic found in {len(updates)} updates")
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
        # Save caption as sidecar .txt so video_agent can read it without re-downloading
        txt_path = local_path.replace(".jpg", ".txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as tf:
                tf.write(caption)
        except Exception as e:
            print(f"[Notify] Could not save caption sidecar: {e}")
        print(f"[Notify] Image downloaded with caption: '{caption}'")
    else:
        print(f"[Notify] Image downloaded (no caption): {local_path}")

    return {"path": local_path, "tags": tags, "caption": caption or ""}


def clear_telegram_queue() -> int:
    """Mark all pending Telegram updates as read. Returns count cleared."""
    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params={"limit": 100}, timeout=20)
        updates = r.json().get("result", [])
        if updates:
            last_id = updates[-1]["update_id"]
            requests.get(f"{BASE_URL}/getUpdates", params={"offset": last_id + 1}, timeout=10)
            print(f"[Notify] Cleared {len(updates)} old messages from queue")
            return len(updates)
        print(f"[Notify] Queue already empty")
        return 0
    except Exception as e:
        print(f"[Notify] clear_telegram_queue failed: {e}")
        return 0


def _download_user_video(file_id: str, file_size: int, duration: int,
                         caption: str, message_id: int) -> dict | None:
    """Download a video from Telegram, apply overlay removal + compression.

    Returns {"path", "tags", "caption"} or None on failure.
    Caller must already have verified file_size <= 20 MB.
    """
    os.makedirs("output/user_videos", exist_ok=True)
    try:
        r2 = requests.get(f"{BASE_URL}/getFile", params={"file_id": file_id}, timeout=15)
        info = r2.json()
        if not info.get("ok"):
            return None
        tg_file_path = info["result"]["file_path"]
    except Exception as e:
        print(f"[Notify] getFile failed: {e}")
        return None

    out_path = f"output/user_videos/user_{message_id}_{file_id[:8]}.mp4"
    try:
        dl = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{tg_file_path}",
            timeout=120, stream=True,
        )
        if dl.status_code != 200:
            return None
        with open(out_path, "wb") as f:
            for chunk in dl.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        print(f"[Notify] Video download failed: {e}")
        return None

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        print(f"[Notify] Video file missing or empty: {out_path}")
        return None

    dur_str = f"{duration}s, " if duration else ""
    print(f"[Notify] Video downloaded: {os.path.basename(out_path)} ({dur_str}caption: {caption[:40]!r})")

    out_path = _remove_video_overlays(out_path)
    compressed = out_path.replace(".mp4", "_c.mp4")
    out_path = _compress_video(out_path, compressed)

    tags = [w.lower() for w in caption.split() if len(w) > 3] if caption else []
    if caption:
        txt = out_path.replace(".mp4", ".txt")
        try:
            with open(txt, "w", encoding="utf-8") as tf:
                tf.write(caption)
        except Exception:
            pass

    return {"path": out_path, "tags": tags, "caption": caption}


def check_telegram_for_images(after_timestamp: float = 0.0) -> list[dict]:
    """
    Check for photos AND videos sent after `after_timestamp` (Unix time).

    Handles message.photo, message.video, and message.document (video files).
    Returns list of {"path": ..., "tags": [...], "caption": ...} dicts.
    Photos saved to output/user_images/; videos to output/user_videos/.
    """
    current_time = time.time()
    cutoff = after_timestamp if after_timestamp > 0 else current_time - 600

    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params={"limit": 100}, timeout=20)
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"[Notify] check_telegram_for_images failed: {e}")
        return []

    results: list[dict] = []

    for update in reversed(updates):  # newest first
        message  = update.get("message", {})
        chat_id  = str(message.get("chat", {}).get("id", ""))
        msg_time = message.get("date", 0)
        caption  = message.get("caption", "") or ""
        msg_id   = message.get("message_id", int(time.time()))

        if chat_id != str(TELEGRAM_CHAT_ID):
            continue
        if msg_time < cutoff:
            print(f"[Notify] Skipping old message (before cutoff): {caption[:40]!r}")
            continue

        # ── Photos ────────────────────────────────────────────────────────────
        photos = message.get("photo", [])
        if photos:
            best = max(photos, key=lambda p: p.get("file_size", 0))
            img_info = download_telegram_photo(best["file_id"], caption=caption)
            if img_info:
                results.append(img_info)
                print(f"[Notify] Photo downloaded: {img_info['path']} caption={caption[:40]!r}")
            continue

        # ── Videos ───────────────────────────────────────────────────────────
        video_msg = message.get("video")
        doc_msg   = message.get("document")
        note_msg  = message.get("video_note")

        file_id   = None
        file_size = 0
        duration  = 0

        if video_msg:
            file_id   = video_msg.get("file_id")
            file_size = video_msg.get("file_size", 0)
            duration  = video_msg.get("duration", 0)
        elif doc_msg and (doc_msg.get("mime_type", "")).startswith("video/"):
            file_id   = doc_msg.get("file_id")
            file_size = doc_msg.get("file_size", 0)
        elif note_msg:
            file_id   = note_msg.get("file_id")
            file_size = note_msg.get("file_size", 0)
            duration  = note_msg.get("duration", 0)

        if not file_id:
            continue

        if file_size > 20 * 1024 * 1024:
            size_mb = file_size // (1024 * 1024)
            print(f"[Notify] Video too large ({size_mb}MB > 20MB limit) — skipping")
            send_message(
                f"\u26a0\ufe0f Video too large ({size_mb}MB). "
                f"Max 20MB. Please compress or trim and resend."
            )
            continue

        vid_info = _download_user_video(file_id, file_size, duration, caption, msg_id)
        if vid_info:
            results.append(vid_info)

    return results


def _remove_video_overlays(input_path: str) -> str:
    """Crop out typical channel overlay areas from a downloaded user video.

    Returns path to the cleaned file (overwrites in-place via a temp file).
    Falls back to the original path if ffmpeg is unavailable or fails.
    """
    import os
    import subprocess
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[Video] ffmpeg not found — skipping overlay removal")
        return input_path

    # Detect dimensions with ffprobe
    width, height = 0, 0
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", input_path],
                capture_output=True, text=True, timeout=15
            )
            parts = probe.stdout.strip().split(",")
            if len(parts) == 2:
                width, height = int(parts[0]), int(parts[1])
        except Exception as e:
            print(f"[Video] ffprobe failed: {e}")

    is_vertical = height > width if (width and height) else False

    if is_vertical:
        # Shorts / TikTok: remove top 10% + bottom 15%
        crop_filter = "crop=iw:ih*0.75:0:ih*0.10"
        removed = "top 10% + bottom 15%"
    else:
        # Landscape: remove top 8% + bottom 12%
        crop_filter = "crop=iw:ih*0.80:0:ih*0.08"
        removed = "top 8% + bottom 12%"

    clean_path = input_path.replace(".mp4", "_clean.mp4")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", input_path,
             "-vf", crop_filter,
             "-c:v", "libx264", "-c:a", "aac",
             "-pix_fmt", "yuv420p",
             clean_path],
            capture_output=True, timeout=180, check=True
        )
        os.replace(clean_path, input_path)
        print(f"[Video] Overlay removal applied: removed {removed}")
        print(f"[Video] Clean video saved: {os.path.basename(input_path)}")
    except Exception as e:
        print(f"[Video] Overlay removal failed: {e} — using original")
        if os.path.exists(clean_path):
            os.remove(clean_path)

    return input_path


def _compress_video(input_path: str, output_path: str, max_mb: int = 15) -> str:
    import subprocess
    file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
    if file_size_mb <= max_mb:
        return input_path

    print(f'[Video] Compressing {file_size_mb:.1f}MB -> target {max_mb}MB')

    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', input_path],
            capture_output=True, text=True, timeout=15
        )
        duration = float(result.stdout.strip())
    except Exception as e:
        print(f'[Video] ffprobe duration failed: {e} — skipping compression')
        return input_path

    target_size_bits = max_mb * 8 * 1024 * 1024
    target_bitrate   = int(target_size_bits / duration)
    video_bitrate    = int(target_bitrate * 0.85)
    audio_bitrate    = 128000

    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-b:v', str(video_bitrate),
            '-b:a', str(audio_bitrate),
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-pix_fmt', 'yuv420p',
            '-preset', 'fast',
            output_path
        ], timeout=120, check=True, capture_output=True)
    except Exception as e:
        print(f'[Video] Compression failed: {e} — using original')
        return input_path

    if os.path.exists(output_path):
        new_size = os.path.getsize(output_path) / (1024 * 1024)
        print(f'[Video] Compressed: {file_size_mb:.1f}MB -> {new_size:.1f}MB')
        return output_path
    return input_path


def check_telegram_for_videos(after_timestamp: float = 0.0) -> list[dict]:
    """
    Check for video messages sent after `after_timestamp`.
    Delegates to _download_user_video() for download + processing.
    Returns list of {"path", "tags", "caption"} dicts, newest-first.
    """
    current_time = time.time()
    cutoff = after_timestamp if after_timestamp > 0 else current_time - 600

    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params={"limit": 100}, timeout=20)
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"[Notify] check_telegram_for_videos failed: {e}")
        return []

    os.makedirs("output/user_videos", exist_ok=True)
    user_videos: list[dict] = []

    for update in reversed(updates):
        message  = update.get("message", {})
        chat_id  = str(message.get("chat", {}).get("id", ""))
        msg_time = message.get("date", 0)
        caption  = message.get("caption", "") or ""
        msg_id   = message.get("message_id", int(time.time()))

        if chat_id != str(TELEGRAM_CHAT_ID):
            continue
        if msg_time < cutoff:
            continue

        file_id   = None
        file_size = 0
        duration  = 0
        video_msg = message.get("video")
        doc_msg   = message.get("document")
        note_msg  = message.get("video_note")

        if video_msg:
            file_id   = video_msg.get("file_id")
            file_size = video_msg.get("file_size", 0)
            duration  = video_msg.get("duration", 0)
        elif doc_msg and (doc_msg.get("mime_type", "")).startswith("video/"):
            file_id   = doc_msg.get("file_id")
            file_size = doc_msg.get("file_size", 0)
        elif note_msg:
            file_id   = note_msg.get("file_id")
            file_size = note_msg.get("file_size", 0)
            duration  = note_msg.get("duration", 0)

        if not file_id:
            continue

        if file_size > 20 * 1024 * 1024:
            size_mb = file_size // (1024 * 1024)
            print(f"[Notify] Video too large ({size_mb}MB > 20MB limit) — skipping")
            send_message(
                f"\u26a0\ufe0f Video too large ({size_mb}MB). "
                f"Max 20MB. Please compress or trim and resend."
            )
            continue

        vid_info = _download_user_video(file_id, file_size, duration, caption, msg_id)
        if vid_info:
            user_videos.append(vid_info)

    return user_videos


def send_video_to_telegram(video_path: str, caption: str, label: str) -> dict:
    """Send a video to Telegram. Uses sendVideo under 50 MB, sendDocument above."""
    import os
    import traceback as _tb

    if not video_path or not os.path.exists(video_path):
        print(f"[Notify] ERROR: {label} — video file does not exist: {video_path}")
        return {"ok": False, "description": "file_not_found"}

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"[Notify] Sending {label}: {file_size_mb:.1f}MB | path: {video_path}")

    if file_size_mb > 50:
        print(f"[Notify] File too large for sendVideo ({file_size_mb:.1f}MB) — sending as document")
        url       = f"{BASE_URL}/sendDocument"
        files_key = "document"
    else:
        url       = f"{BASE_URL}/sendVideo"
        files_key = "video"

    try:
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

        print(f"[Notify] {label} HTTP status: {response.status_code}")
        result = response.json()
        if result.get("ok"):
            print(f"[Notify] {label} sent successfully")
        else:
            print(f"[Notify] {label} FAILED — Telegram error_code: {result.get('error_code')} "
                  f"| description: {result.get('description')} | full response: {result}")
        return result

    except Exception as e:
        print(f"[Notify] ERROR sending {label}: {e}")
        _tb.print_exc()
        raise


def send_topic_confirmation(
    topic_text: str,
    series_name: str | None = None,
    show_characters: list | None = None,
    is_show_topic: bool = False,
) -> None:
    """
    Send a structured topic-confirmed message to Telegram.
    When show_characters is available, lists each character with their real counterpart
    and suggests specific photo captions to maximise image-script matching.
    """
    chars = show_characters or []

    if chars:
        # ── TV show / film with known cast ──────────────────────────────────
        display_name = series_name or topic_text
        char_lines = "\n".join(
            f"- {c['character']} \u2192 {c.get('based_on', '?')} ({c.get('real_role', '')})"
            for c in chars[:5]
        )

        # Build suggested photo captions from the cast
        all_chars  = " ".join(c["character"] for c in chars[:3])
        real_names = " ".join(c.get("based_on", "") for c in chars[:3] if c.get("based_on"))
        first_char = chars[0]["character"]
        first_real = chars[0].get("based_on", "")
        second_char = chars[1]["character"] if len(chars) > 1 else ""
        second_real = chars[1].get("based_on", "") if len(chars) > 1 else ""
        # Pick female character for dedicated caption suggestion if present
        female_line = ""
        for c in chars:
            role = (c.get("real_role") or "").lower()
            name = c.get("character", "")
            real = c.get("based_on", "")
            if any(w in role for w in ("criminologist", "psychologist", "analyst", "researcher", "woman")):
                female_line = f"- Woman character \u2192 caption: {real} real life vs {name}\n"
                break

        msg = (
            f"\u2705 Topic confirmed: {display_name}\n"
            f"\U0001f3ac Show: {series_name or 'TV series'} based on true story\n\n"
            f"\U0001f465 Main characters detected:\n{char_lines}\n\n"
            f"\U0001f4f8 Send photos now (3 minutes) with captions:\n"
            f"- Cast together \u2192 caption: {all_chars} {series_name or ''} cast\n"
            f"- Real vs actor \u2192 caption: {first_real} real vs {first_char}\n"
        )
        if second_char and second_real:
            msg += f"- Real vs actor 2 \u2192 caption: {second_real} real vs {second_char}\n"
        if female_line:
            msg += female_line
        msg += (
            f"- Historical \u2192 caption: {real_names} real photo 1970s\n\n"
            f"\u23f1 No photos = AI generates automatically"
        )
    else:
        # ── Regular crime topic or unknown show ──────────────────────────────
        display_name = topic_text
        if series_name and series_name.lower() not in topic_text.lower():
            display_name = f"{topic_text} {series_name}"

        msg = (
            f"\u2705 Topic confirmed: {display_name}\n\n"
            f"\U0001f4f8 Send photos now (3 minutes) with captions describing exactly "
            f"what is in each photo.\n"
            f"Example: {topic_text} mugshot real photo\n"
            f"Example: {series_name or topic_text} poster cast\n\n"
            f"\u23f1 No photos = AI generates automatically"
        )

    send_message(msg)
    print(f"[Notify] Topic confirmation sent: '{display_name}' "
          f"({'with cast' if chars else 'no cast data'})")


def send_daily_report(stats: dict) -> None:
    msg = (
        f"Daily Report\n\n"
        f"Generated: {stats.get('generated', 0)}\n"
        f"Posted: {stats.get('posted', 0)}\n"
        f"Skipped: {stats.get('skipped', 0)}\n"
        f"Errors: {stats.get('errors', 0)}"
    )
    send_message(msg)
