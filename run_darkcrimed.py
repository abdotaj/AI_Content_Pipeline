# ============================================================
#  run_darkcrimed.py  —  Pipeline entry point for Dark Crime Decoded
#
#  Daily output (1 topic, 4 videos):
#
#  Daily output (1 topic, 4 videos):
#
#    OUTPUT 1 — English long-form (12-20 min) → auto YouTube upload → YouTube link to Telegram
#    OUTPUT 2 — Arabic long-form  (12-20 min) → auto YouTube upload → YouTube link to Telegram
#    OUTPUT 3 — English short (60-90 sec)     → Telegram → post to TikTok / Instagram / Shorts
#    OUTPUT 4 — Arabic short  (60-90 sec)     → Telegram → post to TikTok Arabic / Instagram
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

from agent.research_agent import research_topics, research_series, mark_covered, is_fictional
from agent.script_agent   import write_script, write_short_script, translate_script
from agent.video_agent    import create_video
from agent.notify_agent   import (
    send_message, send_for_manual_posting, send_daily_report,
    send_video_to_telegram, clear_telegram_queue,
    listen_for_content, send_arabic_script_preview, send_english_script_preview,
    check_telegram_for_script, check_telegram_for_images,
)
from agent.publish_agent  import upload_to_youtube
from agents.content_agent import ingest_content_files


def run_pipeline():
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*50}")
    print(f"  Dark Crime Decoded Pipeline — {today}")
    print(f"{'='*50}\n")

    # ── STEP 1: Topic + images ────────────────────────────────
    import time as _time
    pipeline_start_time = _time.time()

    # Priority 1: content/dark_crime/ JSON files (skip Telegram flow)
    ingested = ingest_content_files(content_dir=CONTENT_DIR)

    topic = None
    user_images: list = []

    if ingested:
        print("[1/5] Using script from content files.")
        en_long = next((s for s in ingested if s.get("language") == "english"), ingested[0])

    else:
        # ── 1A: Clear ALL old messages so only new ones are read ──────────────
        print("[1/5] Clearing old Telegram messages...")
        clear_telegram_queue()

        # ── 1B: Tell user pipeline is ready and wait 60s for topic ───────────
        send_message(
            f"Pipeline ready — send your topic now!\n\n"
            f"Examples:\n"
            f"  Frank Lucas\n"
            f"  Al Capone\n"
            f"  Pablo Escobar\n\n"
            f"Waiting 60 seconds..."
        )
        print("[1/5] Waiting 60 seconds for topic...")
        _time.sleep(60)

        # ── 1C: Read ONLY messages sent after the clear ───────────────────────
        print("[1/5] Checking for topic sent in last 60 seconds...")
        telegram_result = check_telegram_for_script(timeout=30)

        if telegram_result:
            raw_input  = telegram_result["content"]
            print(f"[Pipeline] TELEGRAM TOPIC: '{raw_input}'")

            # Parse "frank lucas = American Gangster" or "frank lucas, ..."
            topic_text = raw_input
            if "=" in topic_text:
                topic_text = topic_text.split("=")[0].strip()
            if "," in topic_text:
                topic_text = topic_text.split(",")[0].strip()
            topic_text = topic_text.strip()
            print(f"[Pipeline] Clean topic: '{topic_text}'")

            from agent.script_agent import get_series_for_person as _gsfp
            series_info = _gsfp(topic_text)
            series_name = series_info[0] if series_info else None
            series_type = series_info[1] if series_info else None
            print(f"[Pipeline] Series: {series_name} ({series_type})")

            # ── 1D: Ask for photos now that topic is confirmed ────────────────
            send_message(
                f"Topic confirmed: {topic_text}\n\n"
                f"Send photos now (3 minutes):\n"
                f"  Photo + caption '{topic_text} real photo'\n"
                f"  Photo + caption '{series_name or topic_text} poster'\n\n"
                f"No photos = AI generates automatically"
            )
            print("[1/5] Waiting 3 minutes for photos...")
            _time.sleep(180)

            # ── 1E: Collect images sent AFTER pipeline start ──────────────────
            user_images = check_telegram_for_images(after_timestamp=pipeline_start_time)
            if user_images:
                print(f"[1/5] Found {len(user_images)} image(s) for '{topic_text}'")
                send_message(f"Got {len(user_images)} photo(s) — using in video")
            else:
                print("[1/5] No photos — AI images will be generated")

            # ── 1F: Research exact topic ──────────────────────────────────────
            print(f"[Research] Researching: {topic_text}")
            try:
                research = research_series(topic_text, series_name, user_note=raw_input)
                if research is None:
                    print("[Pipeline] research_series returned None — aborting")
                    return
            except Exception as e:
                print(f"  [WARN] Web research failed for '{topic_text}': {e}")
                research = {}

            # Force correct person — never let research override the user's choice
            if research is not None:
                research["real_person"] = topic_text
                research["series_name"] = series_name or topic_text
            print(f"[Pipeline] Research locked to: '{topic_text}'")

            topic = {
                "topic":        topic_text,
                "niche":        f"Real story behind {series_name or topic_text}",
                "angle":        "",
                "keywords":     [topic_text],
                "search_query": topic_text,
                "series_name":  series_name,
                "research":     research,
            }

        else:
            # ── No topic sent — auto-discover ─────────────────────────────────
            print("[Pipeline] No topic received — auto-researching...")
            send_message(
                f"No topic received — auto-selecting today's story.\n"
                f"Send a name next time to choose the topic."
            )

            try:
                auto_topics = research_topics(count=1)
            except Exception as e:
                send_message(f"Research failed: {e}")
                print(f"[ERROR] Research: {e}")
                return
            topic = auto_topics[0]
            topic_text  = topic.get("topic", "")
            topic_niche = topic.get("niche", "")

            if is_fictional(topic_text, topic_niche):
                print(f"[Pipeline] Fictional topic blocked: '{topic_text}'")
                send_message(
                    f"\u26a0\ufe0f Fictional topic blocked: '{topic_text}'\n\n"
                    f"Dark Crime Decoded only covers REAL true crime stories."
                )
                return

            print(f"[Pipeline] Auto topic: '{topic_text}'")
            series = topic_niche.split("behind")[-1].strip() if "behind" in topic_niche else topic_text
            try:
                research_result = research_series(series, user_note=topic.get("user_note"))
                if research_result is None:
                    print("[Pipeline] research_series returned None — aborting")
                    return
                topic["research"] = research_result
            except Exception as e:
                print(f"  [WARN] Web research failed for '{series}': {e}")
                topic["research"] = {}

            # Collect any images sent after pipeline start (no 3-min wait in auto mode)
            user_images = check_telegram_for_images(after_timestamp=pipeline_start_time)

        print(f"[Pipeline] FINAL TOPIC: {topic.get('topic', '?')}")
        print(f"[Pipeline] Starting pipeline for: {topic.get('topic', '?')}")

        # ── STEP 2: Generate 4 scripts ─────────────────────────
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

    # OUTPUT 1 — English long-form
    en_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_long"
    en_long_path = _make_video(en_long, en_long_id, stats, user_images=user_images)

    # OUTPUT 2 — Arabic long-form
    ar_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_long"
    ar_long_path = _make_video(ar_long, ar_long_id, stats, user_images=user_images)

    # OUTPUT 3 — Arabic short
    ar_short_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_short"
    ar_short_path = _make_video(ar_short, ar_short_id, stats, user_images=user_images)

    # OUTPUT 4 — English short
    en_short_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_short"
    en_short_path = _make_video(en_short, en_short_id, stats, user_images=user_images)

    # Clear user images so they don't bleed into the next run
    import shutil as _shutil
    _img_dir = "output/user_images"
    if os.path.exists(_img_dir):
        _shutil.rmtree(_img_dir)
        os.makedirs(_img_dir)
        print("[Pipeline] User images cleared for next run")

    # ── STEP 5: Upload long videos to YouTube, then send shorts to Telegram ──
    print("\n[5/5] Publishing videos...")

    yt_en_url = None
    if en_long_path:
        try:
            print("[Publish] Uploading English long to YouTube...")
            yt_en_url = upload_to_youtube(en_long_path, en_long)
            send_message(
                f"✅ English Video Published on YouTube!\n\n"
                f"🎬 {en_long.get('title', '')}\n"
                f"🔗 {yt_en_url}\n\n"
                f"Duration: {get_duration(en_long_path)}"
            )
            print(f"  [Publish] English YouTube URL: {yt_en_url}")
        except Exception as e:
            print(f"  [ERROR] English YouTube upload failed: {e}")
            send_message(f"❌ English YouTube upload failed: {e}")

    yt_ar_url = None
    if ar_long_path:
        try:
            print("[Publish] Uploading Arabic long to YouTube...")
            yt_ar_url = upload_to_youtube(ar_long_path, ar_long)
            send_message(
                f"✅ تم نشر الفيديو العربي على يوتيوب!\n\n"
                f"🎬 {ar_long.get('title', '')}\n"
                f"🔗 {yt_ar_url}\n\n"
                f"المدة: {get_duration(ar_long_path)}"
            )
            print(f"  [Publish] Arabic YouTube URL: {yt_ar_url}")
        except Exception as e:
            print(f"  [ERROR] Arabic YouTube upload failed: {e}")
            send_message(f"❌ Arabic YouTube upload failed: {e}")

    if en_short_path:
        try:
            send_for_manual_posting(
                en_short_path, en_short,
                "TikTok + Instagram Reels + YouTube Shorts",
            )
        except Exception as e:
            print(f"  [WARN] Telegram English short send failed: {e}")

    if ar_short_path:
        try:
            send_for_manual_posting(
                ar_short_path, ar_short,
                "TikTok Arabic + Instagram Arabic",
            )
        except Exception as e:
            print(f"  [WARN] Telegram Arabic short send failed: {e}")

    # ── Daily summary ──────────────────────────────────────────
    send_message(
        f"📊 Daily Report — Dark Crime Decoded\n\n"
        f"✅ Generated: 4 videos\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎬 English Long → YouTube\n"
        f"{yt_en_url or '❌ Upload failed'}\n\n"
        f"🎬 Arabic Long → YouTube\n"
        f"{yt_ar_url or '❌ Upload failed'}\n\n"
        f"📱 English Short → Telegram ✅\n"
        f"📱 Arabic Short → Telegram ✅\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    # ── Mark covered + log ─────────────────────────────────────
    series = en_long.get("series") or en_long.get("niche", "").split("behind")[-1].strip()
    if series:
        try:
            mark_covered(series, en_long_id)
        except Exception:
            pass

    log_entry = {
        "date":        today,
        "channel":     "dark_crime",
        "en_long_id":  en_long_id,
        "ar_long_id":  ar_long_id,
        "en_short_id": en_short_id,
        "ar_short_id": ar_short_id,
        "title":       en_long.get("title", ""),
        "niche":       en_long.get("niche", ""),
        "youtube_en":  yt_en_url or "",
        "youtube_ar":  yt_ar_url or "",
    }
    _save_log(log_entry)

    send_daily_report(stats)
    print(f"\nDone. Generated: {stats['generated']} | Posted: {stats['posted']} | Errors: {stats['errors']}\n")


def get_duration(video_path: str) -> str:
    """Return 'MM:SS' duration string for a video file."""
    try:
        from moviepy import VideoFileClip
        clip = VideoFileClip(video_path)
        duration = clip.duration
        clip.close()
        mins = int(duration // 60)
        secs = int(duration % 60)
        return f"{mins}:{secs:02d}"
    except Exception:
        return "unknown"


def _make_video(script_data: dict, video_id: str, stats: dict, user_images: list | None = None) -> str:
    """Create a video using ElevenLabs + Pollinations, update stats, return path."""
    try:
        path = create_video(script_data, video_id, user_images=user_images)
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
