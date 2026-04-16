# ============================================================
#  run_darkcrimed.py  —  Pipeline entry point for Dark Crime Decoded
#
#  Daily output (1 topic, 4 videos):
#
#    OUTPUT 1 — English long-form (10-12 min)
#               Auto-uploaded to YouTube
#
#    OUTPUT 2 — Arabic long-form (10-12 min)
#               Auto-uploaded to YouTube
#
#    OUTPUT 3 — English short (55 sec)
#               Sent to Telegram → POST TO: TikTok + Instagram + YouTube Shorts
#
#    OUTPUT 4 — Arabic short (55 sec)
#               Sent to Telegram → POST TO: TikTok Arabic + Instagram Arabic
#
#  Fully automated — ElevenLabs cloned voices generate audio,
#  Pollinations API generates AI images. No human intervention
#  needed except approving short clips on Telegram for posting.
# ============================================================
import os
import sys
import json
import uuid
import datetime
from pathlib import Path

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(__file__))

# Patch 'config' BEFORE any agent import
import config_darkcrimed
sys.modules["config"] = config_darkcrimed

from config_darkcrimed import (
    FINAL_DIR, CONTENT_DIR, YOUTUBE_TOKEN_FILE, SHORT_VIDEO_DURATION,
)

# Write YouTube token from env secret (CI) or use existing file (local)
_yt_token_json = os.getenv("YOUTUBE_TOKEN_JSON_DARKCRIMED")
if _yt_token_json:
    Path(YOUTUBE_TOKEN_FILE).write_text(_yt_token_json, encoding="utf-8")

from agent.research_agent import research_topics, research_series, mark_covered
from agent.script_agent   import write_script, write_short_script, translate_script
from agent.video_agent    import create_video
from agent.notify_agent   import (
    send_message, send_for_manual_posting, send_daily_report,
    listen_for_content, send_arabic_script_preview, send_english_script_preview,
    check_telegram_for_script,
)
from agent.publish_agent  import upload_to_youtube
from agents.content_agent import ingest_content_files


def run_pipeline():
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*50}")
    print(f"  Dark Crime Decoded Pipeline — {today}")
    print(f"{'='*50}\n")

    send_message(f"Dark Crime Decoded — Pipeline starting {today}")

    # ── STEP 1: Determine content source (priority order) ─────
    print("[1/5] Checking for user-provided content...")

    # Priority 1: content/dark_crime/ JSON files
    ingested = ingest_content_files(content_dir=CONTENT_DIR)

    telegram_input = None
    topic = None

    if ingested:
        print("[1/5] Using script from content files.")
        en_long = next((s for s in ingested if s.get("language") == "english"), ingested[0])

    else:
        # Priority 2: Telegram inbox — only messages from the last 10 minutes
        print("[1/5] Checking Telegram for user-provided topic...")
        telegram_input = check_telegram_for_script(timeout=15)

        if telegram_input and telegram_input.get("type") == "research_note":
            content     = telegram_input["content"]
            is_detailed = telegram_input.get("is_detailed", False)

            if is_detailed:
                print(f"[1/5] Research note from Telegram: {content[:100]}...")
                send_message(f"Got your research note!\nResearching deeper: {content[:100]}...")
                topic_name = content  # full note used as research seed
            else:
                print(f"[1/5] Topic from Telegram: {content!r}")
                send_message(f"Researching: {content}")
                topic_name = content

            topic = {
                "topic":        topic_name,
                "niche":        topic_name,
                "angle":        "",
                "keywords":     [topic_name],
                "search_query": topic_name,
                "user_note":    content,      # always carry the original note
            }

        else:
            # Priority 3: auto research
            print("[1/5] No recent Telegram message — researching trending topic...")
            listen_for_content(timeout=30)

        if not ingested and not telegram_input:
            # Auto research path
            try:
                topics = research_topics(count=1)
            except Exception as e:
                send_message(f"Research failed: {e}")
                print(f"[ERROR] Research: {e}")
                return
            topic = topics[0]

        if topic:
            # Research real facts for topic (content files and full-script paths skip this)
            print("[1b] Web-researching real facts...")
            niche  = topic.get("niche", "")
            series = niche.split("behind")[-1].strip() if "behind" in niche else topic.get("topic", "")
            try:
                user_note = topic.get("user_note")
                topic["research"] = research_series(series, user_note=user_note)
            except Exception as e:
                print(f"  [WARN] Web research failed for '{series}': {e}")
                topic["research"] = {}

            # ── STEP 2: Generate 4 scripts ─────────────────────
            print("\n[2/5] Writing scripts...")
            try:
                en_long = write_script(topic, language="english")
                print("  [2/5] English long script done")
            except Exception as e:
                send_message(f"Script writing failed: {e}")
                print(f"[ERROR] Script: {e}")
                return

    try:
        en_short = write_short_script(en_long)
        print("  [2/5] English short script done")
    except Exception as e:
        send_message(f"Short script writing failed: {e}")
        print(f"[ERROR] Short script: {e}")
        return

    try:
        ar_long = translate_script(en_long)
        print("  [2/5] Arabic long script done")
    except Exception as e:
        send_message(f"Arabic translation failed: {e}")
        print(f"[ERROR] Arabic translation: {e}")
        return

    try:
        ar_short = translate_script(en_short)
        print("  [2/5] Arabic short script done")
    except Exception as e:
        send_message(f"Arabic short translation failed: {e}")
        print(f"[ERROR] Arabic short translation: {e}")
        return

    # ── STEP 3: Send scripts to Telegram for review (non-blocking) ────────────
    print("\n[3/5] Sending scripts to Telegram for review...")
    for fn, script, label in [
        (send_arabic_script_preview,  ar_short, "Arabic SHORT script (55 sec)"),
        (send_arabic_script_preview,  ar_long,  "Arabic LONG script (10-12 min)"),
        (send_english_script_preview, en_short, "English SHORT script (55 sec)"),
        (send_english_script_preview, en_long,  "English LONG script (10-12 min)"),
    ]:
        try:
            fn(script, label=label)
        except Exception as e:
            print(f"  [WARN] Script preview failed ({label}): {e}")
    print("  Scripts sent — continuing pipeline immediately.")

    # ── STEP 4: Generate all 4 videos ─────────────────────────
    print("\n[4/5] Generating videos...")

    # OUTPUT 1 — English long-form → YouTube
    en_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_long"
    en_long_path = _make_video(en_long, en_long_id, stats)

    # OUTPUT 2 — Arabic long-form → YouTube
    ar_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_long"
    ar_long_path = _make_video(ar_long, ar_long_id, stats)

    # OUTPUT 3 — Arabic short → Telegram (first: gets 2-3x more views)
    ar_short_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_short"
    ar_short_path = _make_video(ar_short, ar_short_id, stats)

    # OUTPUT 4 — English short → Telegram
    en_short_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_short"
    en_short_path = _make_video(en_short, en_short_id, stats)

    # ── STEP 5: Publish ────────────────────────────────────────
    print("\n[5/5] Publishing...")

    yt_en_url = yt_ar_url = ""
    yt_en_ok  = yt_ar_ok  = False

    if en_long_path:
        try:
            yt_en_url = upload_to_youtube(en_long_path, en_long)
            yt_en_ok  = True
            stats["posted"] += 1
            print(f"  YouTube (English): {yt_en_url or 'uploaded (no URL)'}")
        except Exception as e:
            print(f"  [ERROR] YouTube English upload: {e}")
            send_message(f"YouTube English upload failed: {e}")
            stats["errors"] += 1

    if ar_long_path:
        try:
            yt_ar_url = upload_to_youtube(ar_long_path, ar_long)
            yt_ar_ok  = True
            stats["posted"] += 1
            print(f"  YouTube (Arabic): {yt_ar_url or 'uploaded (no URL)'}")
        except Exception as e:
            print(f"  [ERROR] YouTube Arabic upload: {e}")
            send_message(f"YouTube Arabic upload failed: {e}")
            stats["errors"] += 1

    # Inject long-video URL into short scripts so descriptions link back
    if yt_en_url:
        en_short["long_video_url"] = yt_en_url
        ar_short["long_video_url"] = yt_en_url

    # Arabic short first — gets 2-3x more views, post immediately
    if ar_short_path:
        try:
            send_for_manual_posting(ar_short_path, ar_short, "TikTok Arabic + Instagram Arabic")
        except Exception as e:
            print(f"  [WARN] Telegram Arabic short send failed: {e}")

    if en_short_path:
        try:
            send_for_manual_posting(en_short_path, en_short, "TikTok + Instagram + YouTube Shorts")
        except Exception as e:
            print(f"  [WARN] Telegram English short send failed: {e}")

    # ── Mark covered + log ─────────────────────────────────────
    series = en_long.get("series") or en_long.get("niche", "").split("behind")[-1].strip()
    if series:
        try:
            mark_covered(series, en_long_id)
        except Exception:
            pass

    log_entry = {
        "date":          today,
        "channel":       "dark_crime",
        "en_long_id":    en_long_id,
        "ar_long_id":    ar_long_id,
        "en_short_id":   en_short_id,
        "ar_short_id":   ar_short_id,
        "title":         en_long.get("title", ""),
        "niche":         en_long.get("niche", ""),
        "youtube_en":    yt_en_url,
        "youtube_ar":    yt_ar_url,
    }
    _save_log(log_entry)

    en_status = yt_en_url if yt_en_url else ("uploaded (no URL)" if yt_en_ok else "failed")
    ar_status = yt_ar_url if yt_ar_url else ("uploaded (no URL)" if yt_ar_ok else "failed")
    send_message(
        f"Dark Crime Decoded — {en_long.get('title', en_long_id)}\n"
        f"OUTPUT 1 — YouTube English (long): {en_status}\n"
        f"OUTPUT 2 — YouTube Arabic (long): {ar_status}\n"
        f"OUTPUT 3 — English short sent to Telegram\n"
        f"OUTPUT 4 — Arabic short sent to Telegram"
    )

    send_daily_report(stats)
    print(f"\nDone. Generated: {stats['generated']} | Posted: {stats['posted']} | Errors: {stats['errors']}\n")


def _make_video(script_data: dict, video_id: str, stats: dict) -> str:
    """Create a video using ElevenLabs + Pollinations, update stats, return path."""
    try:
        path = create_video(script_data, video_id)
        if path and Path(path).exists():
            stats["generated"] += 1
            print(f"  Video ready: {path}")
            return path
        raise RuntimeError("create_video returned no file")
    except Exception as e:
        print(f"  [ERROR] {video_id}: {e}")
        send_message(f"Video creation failed for {video_id}: {e}")
        stats["errors"] += 1
        return ""


def _save_log(entry: dict):
    log_path = os.path.join("output", "dark_crime", "publish_log.jsonl")
    Path("output/dark_crime").mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run_pipeline()
