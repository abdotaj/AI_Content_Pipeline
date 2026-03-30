# ============================================================
#  run_content.py  —  Process a single dropped content file
#  Usage: python run_content.py content/my_topic.json
#
#  Content file format (JSON):
#  {
#    "topic":    "How AI is replacing Hollywood actors",   # required
#    "niche":    "AI Deepfakes & Real vs Fake",            # optional, default: "AI & Tech news"
#    "angle":    "The shocking truth behind blockbusters", # optional
#    "language": "english"                                 # optional: "english"|"arabic", default: both
#  }
# ============================================================
import os
import sys
import json
import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from agents.script_agent  import write_script
from agents.video_agent   import create_video
from agents.notify_agent  import send_message, send_video_preview, send_daily_report
from agents.publish_agent import publish_video


def run_content_file(file_path: str):
    with open(file_path) as f:
        content = json.load(f)

    if "topic" not in content:
        print(f"[Content] ERROR: '{file_path}' must have a 'topic' field.")
        sys.exit(1)

    topic = {
        "topic":        content["topic"],
        "niche":        content.get("niche", "AI & Tech news"),
        "angle":        content.get("angle", ""),
        "keywords":     content.get("keywords", [content["topic"]]),
        "search_query": content.get("search_query", content["topic"].split()[0]),
    }

    requested_lang = content.get("language")
    languages = [requested_lang] if requested_lang else ["arabic", "english"]

    today = datetime.date.today().isoformat()
    stem  = Path(file_path).stem
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    send_message(
        f"Content drop: {topic['topic']}\n"
        f"Niche: {topic['niche']}\n"
        f"Generating {len(languages)} video(s)..."
    )

    # ── Script + Video ──────────────────────────────────────
    video_queue = []
    for lang in languages:
        try:
            script_data = write_script(topic, language=lang)
            video_id    = f"{today}_{stem}_{lang}"
            video_path  = create_video(script_data, video_id)
            if video_path and Path(video_path).exists():
                video_queue.append((video_path, script_data, video_id))
                stats["generated"] += 1
            else:
                print(f"[Content] Video assembly failed for {lang}")
                stats["errors"] += 1
        except Exception as e:
            print(f"[Content] Error generating {lang} video: {e}")
            send_message(f"Error generating {lang} video: {e}")
            stats["errors"] += 1

    if not video_queue:
        send_message("No videos generated. Check logs.")
        return

    # ── Notify + Approve + Publish ──────────────────────────
    for video_path, script_data, video_id in video_queue:
        try:
            decision = send_video_preview(video_path, script_data, video_id)
            if decision == "approve":
                results = publish_video(video_path, script_data)
                _save_log({
                    "date":      today,
                    "source":    file_path,
                    "video_id":  video_id,
                    "title":     script_data["title"],
                    "niche":     script_data["niche"],
                    "youtube":   results.get("youtube", ""),
                    "facebook":  results.get("facebook", ""),
                    "tiktok":    results.get("tiktok", ""),
                    "instagram": results.get("instagram", ""),
                })
                stats["posted"] += 1
                send_message(
                    f"Posted: {script_data['title']}\n"
                    f"YouTube:   {results.get('youtube', '-')}\n"
                    f"Facebook:  {results.get('facebook', '-')}\n"
                    f"TikTok:    {results.get('tiktok', '-')}\n"
                    f"Instagram: {results.get('instagram', '-')}"
                )
            else:
                print(f"[Content] Skipped: {video_id}")
                stats["skipped"] += 1
        except Exception as e:
            print(f"[Content] Error publishing {video_id}: {e}")
            send_message(f"Error publishing {video_id}: {e}")
            stats["errors"] += 1

    send_daily_report(stats)


def _save_log(entry: dict):
    log_path = "output/publish_log.jsonl"
    Path("output").mkdir(exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_content.py content/my_topic.json")
        sys.exit(1)
    run_content_file(sys.argv[1])
