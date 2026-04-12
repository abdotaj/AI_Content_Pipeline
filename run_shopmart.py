# ============================================================
#  run_shopmart.py  —  Pipeline entry point for Shopmart Global
#
#  Daily output:
#    • 1 short video (60 sec) → auto-post to YouTube Shorts
#    • Same video → sent to Telegram for manual posting to TikTok + Instagram
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
import config_shopmart
sys.modules["config"] = config_shopmart

from config_shopmart import VIDEOS_PER_DAY, FINAL_DIR, CONTENT_DIR, YOUTUBE_TOKEN_FILE, NICHES, OUTPUT_DIR

# Write YouTube token from env secret (CI) or use existing file (local)
_yt_token_json = os.getenv("YOUTUBE_TOKEN_JSON_SHOPMART")
if _yt_token_json:
    Path(YOUTUBE_TOKEN_FILE).write_text(_yt_token_json, encoding="utf-8")

from agent.research_agent import research_topics, research_series, mark_covered
from agent.script_agent   import write_scripts
from agent.video_agent    import create_video
from agent.notify_agent   import send_message, send_for_manual_posting, send_daily_report, listen_for_content
from agent.publish_agent  import upload_to_youtube
from agents.content_agent import ingest_content_files


def run_pipeline():
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*50}")
    print(f"  Shopmart Global Pipeline — {today}")
    print(f"{'='*50}\n")

    send_message(f"Shopmart Global — Pipeline starting {today}")

    listen_for_content(timeout=30)

    print(f"[1/4] Checking {CONTENT_DIR}/ for user-provided files...")
    scripts = ingest_content_files(content_dir=CONTENT_DIR)

    if scripts:
        print(f"[1/4] Using {len(scripts)} script(s) from content files.")
    else:
        print("[1/4] No content files found — researching trending topics...")
        try:
            topics = research_topics(count=VIDEOS_PER_DAY, niches=NICHES)
        except Exception as e:
            send_message(f"Research failed: {e}")
            print(f"[ERROR] Research: {e}")
            return

        print("[1b] Web-researching product facts...")
        for topic in topics:
            niche  = topic.get("niche", "")
            series = niche.split("—")[-1].strip() if "—" in niche else topic.get("topic", "")
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
        video_id = f"{today}_shopmart_{i+1}"
        try:
            video_path = create_video(script_data, video_id)
            if not video_path or not Path(video_path).exists():
                stats["errors"] += 1
                continue

            stats["generated"] += 1
            print(f"  Video ready: {video_path}")

        except Exception as e:
            print(f"  [ERROR] Video {i+1}: {e}")
            stats["errors"] += 1
            continue

        print(f"\n[4/4] Publishing video {i+1}...")

        # ── Short video → YouTube Shorts (auto-post, no approval needed) ──
        try:
            yt_url = upload_to_youtube(video_path, script_data)
            stats["posted"] += 1
        except Exception as e:
            yt_url = ""
            print(f"  [ERROR] YouTube upload: {e}")
            send_message(f"YouTube upload failed for {video_id}: {e}")
            stats["errors"] += 1

        # ── Same video → Telegram for manual posting ───────────────────────
        try:
            send_for_manual_posting(
                video_path, script_data,
                "TikTok + Instagram"
            )
        except Exception as e:
            print(f"  [WARN] Telegram send failed: {e}")

        log_entry = {
            "date": today, "channel": "shopmart", "video_id": video_id,
            "title": script_data.get("title", ""), "niche": script_data.get("niche", ""),
            "youtube": yt_url,
        }
        _save_log(log_entry)

        send_message(
            f"Shopmart Global — {script_data.get('title', video_id)}\n"
            f"YouTube Shorts: {yt_url or 'failed'}\n"
            f"Video sent to Telegram for manual posting"
        )

    send_daily_report(stats)
    print(f"\nDone. Generated: {stats['generated']} | Posted: {stats['posted']} | Errors: {stats['errors']}\n")


def _save_log(entry: dict):
    log_path = os.path.join(OUTPUT_DIR, "publish_log.jsonl")
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run_pipeline()
