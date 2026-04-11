# ============================================================
#  run_darkcrimed.py  —  Pipeline entry point for Dark Crime Decoded
#
#  Daily output:
#    OUTPUT 1 — English long-form (10-12 min)
#               Auto-uploaded to YouTube Dark Crime Decoded channel
#
#    OUTPUT 2 — English short clip (55 sec)
#               Cut from first 55 sec of the long English video
#               Sent to Telegram → POST TO: TikTok + Instagram + YouTube Shorts
#
#    OUTPUT 3 — Arabic short video (55 sec)
#               Separate video with Arabic voiceover
#               Sent to Telegram → POST TO: TikTok Arabic + Instagram Arabic
#               NOT uploaded to YouTube automatically
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
    VIDEOS_PER_DAY, FINAL_DIR, CONTENT_DIR, YOUTUBE_TOKEN_FILE,
    SHORT_CLIP_DURATION,
)

# Write YouTube token from env secret (CI) or use existing file (local)
_yt_token_json = os.getenv("YOUTUBE_TOKEN_JSON_DARKCRIMED")
if _yt_token_json:
    Path(YOUTUBE_TOKEN_FILE).write_text(_yt_token_json, encoding="utf-8")

from agent.research_agent import research_topics, research_series, mark_covered
from agent.script_agent   import write_script, translate_script
from agent.video_agent    import create_video, cut_short_clip
from agent.notify_agent   import send_message, send_for_manual_posting, send_daily_report, listen_for_content
from agent.publish_agent  import upload_to_youtube
from agents.content_agent import ingest_content_files


def run_pipeline():
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*50}")
    print(f"  Dark Crime Decoded Pipeline — {today}")
    print(f"{'='*50}\n")

    send_message(f"Dark Crime Decoded — Pipeline starting {today}")

    listen_for_content(timeout=30)

    # ── STEP 1: Get scripts ────────────────────────────────────
    print(f"[1/4] Checking {CONTENT_DIR}/ for user-provided files...")
    ingested = ingest_content_files(content_dir=CONTENT_DIR)

    if ingested:
        # ingest_content_files returns a list of script dicts; use the first english one
        print(f"[1/4] Using script from content files.")
        en_script = next((s for s in ingested if s.get("language") == "english"), ingested[0])
    else:
        print("[1/4] No content files found — researching trending topics...")
        try:
            topics = research_topics(count=1)
        except Exception as e:
            send_message(f"Research failed: {e}")
            print(f"[ERROR] Research: {e}")
            return

        topic = topics[0]

        print("[1b] Web-researching real facts...")
        niche  = topic.get("niche", "")
        series = niche.split("behind")[-1].strip() if "behind" in niche else topic.get("topic", "")
        try:
            topic["research"] = research_series(series)
        except Exception as e:
            print(f"  [WARN] Web research failed for '{series}': {e}")
            topic["research"] = {}

        print("\n[2/4] Writing English script...")
        try:
            en_script = write_script(topic, language="english")
        except Exception as e:
            send_message(f"Script writing failed: {e}")
            print(f"[ERROR] Scripts: {e}")
            return

    # ── STEP 2: Translate to Arabic ────────────────────────────
    print("[2b/4] Translating script to Arabic...")
    try:
        ar_script = translate_script(en_script)
    except Exception as e:
        send_message(f"Arabic translation failed: {e}")
        print(f"[ERROR] Translation: {e}")
        ar_script = None

    # ── STEP 3: Generate English long-form video ───────────────
    print("\n[3/4] Creating English long-form video...")
    en_video_id = f"{today}_{uuid.uuid4().hex[:8]}_english"
    try:
        en_video_path = create_video(en_script, en_video_id)
        if not en_video_path or not Path(en_video_path).exists():
            raise RuntimeError("create_video returned no file")
        stats["generated"] += 1
        print(f"  English long video ready: {en_video_path}")
    except Exception as e:
        print(f"  [ERROR] English video: {e}")
        send_message(f"English video creation failed: {e}")
        stats["errors"] += 1
        return

    # ── STEP 4: OUTPUT 1 — Auto-upload English long video to YouTube ──
    print("\n[4/4] Publishing...")
    yt_url = ""
    try:
        yt_url = upload_to_youtube(en_video_path, en_script)
        stats["posted"] += 1
        print(f"  YouTube: {yt_url}")
    except Exception as e:
        print(f"  [ERROR] YouTube upload: {e}")
        send_message(f"YouTube upload failed for {en_video_id}: {e}")
        stats["errors"] += 1

    # ── OUTPUT 2 — English short clip (55 sec) → Telegram ─────
    en_short_path = en_script.get("short_clip_path", "")
    if not en_short_path or not Path(en_short_path).exists():
        en_short_path = cut_short_clip(en_video_path, en_video_id, duration=SHORT_CLIP_DURATION)
    if en_short_path and Path(en_short_path).exists():
        try:
            send_for_manual_posting(
                en_short_path, en_script,
                "TikTok + Instagram + YouTube Shorts"
            )
        except Exception as e:
            print(f"  [WARN] Telegram English short send failed: {e}")
    else:
        print("  [WARN] English short clip not found — skipping")

    # ── OUTPUT 3 — Arabic short video (55 sec) → Telegram ─────
    if ar_script:
        print("\n  Creating Arabic short video...")
        ar_video_id = f"{today}_{uuid.uuid4().hex[:8]}_arabic"
        try:
            ar_video_path = create_video(ar_script, ar_video_id)
            if not ar_video_path or not Path(ar_video_path).exists():
                raise RuntimeError("create_video returned no file")
            stats["generated"] += 1
            print(f"  Arabic video ready: {ar_video_path}")

            ar_short_path = ar_script.get("short_clip_path", "")
            if not ar_short_path or not Path(ar_short_path).exists():
                ar_short_path = cut_short_clip(ar_video_path, ar_video_id, duration=SHORT_CLIP_DURATION)

            if ar_short_path and Path(ar_short_path).exists():
                send_for_manual_posting(
                    ar_short_path, ar_script,
                    "TikTok Arabic + Instagram Arabic"
                )
            else:
                print("  [WARN] Arabic short clip not found — skipping")

        except Exception as e:
            print(f"  [ERROR] Arabic video: {e}")
            send_message(f"Arabic video creation failed: {e}")
            stats["errors"] += 1
    else:
        print("  [SKIP] Arabic video skipped (translation failed)")

    # ── Mark covered + log ────────────────────────────────────
    series = en_script.get("series") or en_script.get("niche", "").split("behind")[-1].strip()
    if series:
        try:
            mark_covered(series, en_video_id)
        except Exception:
            pass

    log_entry = {
        "date":       today,
        "channel":    "dark_crime",
        "video_id":   en_video_id,
        "title":      en_script.get("title", ""),
        "niche":      en_script.get("niche", ""),
        "youtube":    yt_url,
        "en_short":   en_short_path if en_short_path else "",
        "ar_video_id": ar_video_id if ar_script else "",
    }
    _save_log(log_entry)

    send_message(
        f"Dark Crime Decoded — {en_script.get('title', en_video_id)}\n"
        f"OUTPUT 1 — YouTube (long): {yt_url or 'failed'}\n"
        f"OUTPUT 2 — English short sent to Telegram\n"
        f"OUTPUT 3 — Arabic short sent to Telegram"
    )

    send_daily_report(stats)
    print(f"\nDone. Generated: {stats['generated']} | Posted: {stats['posted']} | Errors: {stats['errors']}\n")


def _save_log(entry: dict):
    log_path = os.path.join("output", "dark_crime", "publish_log.jsonl")
    Path("output/dark_crime").mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run_pipeline()
