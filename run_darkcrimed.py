# ============================================================
#  run_darkcrimed.py  —  Pipeline entry point for Dark Crime Decoded
#
#  Daily output:
#    • 1 long-form video (12 min) → auto-post to YouTube
#    • 1 short clip (55 sec) cut from the long video
#      → sent to Telegram for manual posting to TikTok, Instagram Reels,
#        and YouTube Shorts
# ============================================================
import os
import sys
import json
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
from agent.script_agent   import write_scripts
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

    print(f"[1/4] Checking {CONTENT_DIR}/ for user-provided files...")
    scripts = ingest_content_files(content_dir=CONTENT_DIR)

    if scripts:
        print(f"[1/4] Using {len(scripts)} script(s) from content files.")
    else:
        print("[1/4] No content files found — researching trending topics...")
        try:
            topics = research_topics(count=VIDEOS_PER_DAY)
        except Exception as e:
            send_message(f"Research failed: {e}")
            print(f"[ERROR] Research: {e}")
            return

        print("[1b] Web-researching real facts...")
        for topic in topics:
            niche  = topic.get("niche", "")
            series = niche.split("behind")[-1].strip() if "behind" in niche else topic.get("topic", "")
            try:
                topic["research"] = research_series(series)
            except Exception as e:
                print(f"  [WARN] Web research failed for '{series}': {e}")
                topic["research"] = {}

        print("\n[2/4] Writing scripts...")
        try:
            scripts = write_scripts(topics)
        except Exception as e:
            send_message(f"Script writing failed: {e}")
            print(f"[ERROR] Scripts: {e}")
            return

    print("\n[3/4] Creating videos...")
    for i, script_data in enumerate(scripts):
        video_id = f"{today}_darkcrimed_{i+1}"
        try:
            # Build long-form video (12 min); cut_short_clip is called inside create_video
            video_path = create_video(script_data, video_id)
            if not video_path or not Path(video_path).exists():
                stats["errors"] += 1
                continue

            stats["generated"] += 1
            print(f"  Long video ready: {video_path}")

            # Ensure a 55-sec short clip exists (create_video stores it in script_data)
            short_path = script_data.get("short_clip_path", "")
            if not short_path or not Path(short_path).exists():
                short_path = cut_short_clip(video_path, video_id, duration=SHORT_CLIP_DURATION)
                script_data["short_clip_path"] = short_path

        except Exception as e:
            print(f"  [ERROR] Video {i+1}: {e}")
            stats["errors"] += 1
            continue

        print(f"\n[4/4] Publishing video {i+1}...")

        # ── Long video → YouTube (auto-post, no approval needed) ──────────
        try:
            yt_url = upload_to_youtube(video_path, script_data)
            stats["posted"] += 1
        except Exception as e:
            yt_url = ""
            print(f"  [ERROR] YouTube upload: {e}")
            send_message(f"YouTube upload failed for {video_id}: {e}")
            stats["errors"] += 1

        # ── Short clip → Telegram for manual posting ───────────────────────
        short_path = script_data.get("short_clip_path", "")
        if short_path and Path(short_path).exists():
            try:
                send_for_manual_posting(
                    short_path, script_data,
                    "TikTok + Instagram Reels + YouTube Shorts"
                )
            except Exception as e:
                print(f"  [WARN] Telegram short clip send failed: {e}")
        else:
            print("  [WARN] Short clip not found — skipping Telegram send")

        # Mark topic covered and log
        series = script_data.get("series") or script_data.get("niche", "").split("behind")[-1].strip()
        if series:
            try:
                mark_covered(series, video_id)
            except Exception:
                pass

        log_entry = {
            "date": today, "channel": "dark_crime", "video_id": video_id,
            "title": script_data.get("title", ""), "niche": script_data.get("niche", ""),
            "youtube": yt_url,
            "short_clip": short_path,
        }
        _save_log(log_entry)

        send_message(
            f"Dark Crime Decoded — {script_data.get('title', video_id)}\n"
            f"YouTube (long): {yt_url or 'failed'}\n"
            f"Short clip sent to Telegram for manual posting"
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
