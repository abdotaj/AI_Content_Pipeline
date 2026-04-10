# ============================================================
#  run_shopmart.py  —  Pipeline entry point for Shopmart Global
#  Patches 'config' module before any agent import so all agents
#  pick up Shopmart settings (niches, output paths, token file).
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

from config_shopmart import VIDEOS_PER_DAY, FINAL_DIR, CONTENT_DIR, YOUTUBE_TOKEN_FILE

# Write YouTube token from env secret (CI) or use existing file (local)
_yt_token_json = os.getenv("YOUTUBE_TOKEN_JSON_SHOPMART")
if _yt_token_json:
    Path(YOUTUBE_TOKEN_FILE).write_text(_yt_token_json, encoding="utf-8")

from agent.research_agent import research_topics, research_series, mark_covered
from agent.script_agent   import write_scripts
from agent.video_agent    import create_video
from agent.notify_agent   import send_message, send_video_preview, send_daily_report, listen_for_content
from agent.publish_agent  import publish_video
from agents.content_agent import ingest_content_files


def run_pipeline():
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*50}")
    print(f"  Shopmart Global Pipeline — {today}")
    print(f"{'='*50}\n")

    send_message(f"*Shopmart Global* — Pipeline starting {today}")

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
    video_queue = []
    for i, script_data in enumerate(scripts):
        video_id = f"{today}_shopmart_{i+1}"
        try:
            video_path = create_video(script_data, video_id)
            if video_path and Path(video_path).exists():
                video_queue.append((video_path, script_data, video_id))
                stats["generated"] += 1
                print(f"  Video {i+1} ready: {video_path}")
            else:
                stats["errors"] += 1
        except Exception as e:
            print(f"  [ERROR] Video {i+1}: {e}")
            stats["errors"] += 1

    if not video_queue:
        send_message("No videos generated today. Check logs.")
        return

    print(f"\n[4/4] Sending {len(video_queue)} video(s) for approval...")
    for video_path, script_data, video_id in video_queue:
        try:
            decision = send_video_preview(video_path, script_data, video_id)
            if decision == "approve":
                results = publish_video(video_path, script_data)
                log_entry = {
                    "date": today, "channel": "shopmart", "video_id": video_id,
                    "title": script_data["title"], "niche": script_data["niche"],
                    "youtube": results.get("youtube", ""), "facebook": results.get("facebook", ""),
                    "tiktok": results.get("tiktok", ""),  "instagram": results.get("instagram", ""),
                }
                _save_log(log_entry)
                stats["posted"] += 1
                send_message(
                    f"Posted *{script_data['title']}*\n"
                    f"YouTube: {results.get('youtube', '-')}\n"
                    f"Instagram: {results.get('instagram', '-')}"
                )
            else:
                stats["skipped"] += 1
        except Exception as e:
            print(f"  [ERROR] Publishing {video_id}: {e}")
            send_message(f"Error publishing {video_id}: {e}")
            stats["errors"] += 1

    send_daily_report(stats)
    print(f"\nDone. Generated: {stats['generated']} | Posted: {stats['posted']} | Skipped: {stats['skipped']}\n")


def _save_log(entry: dict):
    log_path = os.path.join("output", "shopmart", "publish_log.jsonl")
    Path("output/shopmart").mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run_pipeline()
