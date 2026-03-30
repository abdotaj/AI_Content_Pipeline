# ============================================================
#  main.py  —  Orchestrator: runs the full daily pipeline
# ============================================================
import os
import sys
import json
import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from config import VIDEOS_PER_DAY, FINAL_DIR
from agent.research_agent import research_topics
from agent.script_agent   import write_scripts, write_script
from agent.video_agent    import create_video
from agent.notify_agent   import send_message, send_video_preview, send_daily_report, listen_for_content
from agent.publish_agent  import publish_video


def run_pipeline():
    """Full daily pipeline: research → script → video → notify → publish."""

    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*50}")
    print(f"  Content Pipeline — {today}")
    print(f"{'='*50}\n")

    send_message(f"*Pipeline starting* — {today}\nGenerating {VIDEOS_PER_DAY} videos...")

    # ── STEP 0: Check for Telegram content drops ────────────
    listen_for_content(timeout=30)

    # ── STEP 1: Research or use pending content ─────────────
    pending_dir   = Path("content/pending")
    processed_dir = Path("content/processed")
    processed_dir.mkdir(parents=True, exist_ok=True)

    pending_files = sorted(pending_dir.glob("*.txt")) + sorted(pending_dir.glob("*.docx")) \
        if pending_dir.exists() else []

    if pending_files:
        print(f"[1/4] Using {len(pending_files)} pending content file(s) from Telegram...")
        scripts = []
        for pf in pending_files:
            try:
                if pf.suffix == ".docx":
                    try:
                        import docx
                        raw_text = "\n".join(p.text for p in docx.Document(str(pf)).paragraphs)
                    except ImportError:
                        print(f"[WARN] python-docx not installed, skipping {pf.name}")
                        continue
                else:
                    raw_text = pf.read_text(encoding="utf-8")

                topic = {
                    "topic":        raw_text[:200],
                    "niche":        "AI & Tech news",
                    "angle":        "",
                    "keywords":     [raw_text.split()[0]] if raw_text.split() else ["technology"],
                    "search_query": raw_text.split()[0] if raw_text.split() else "technology",
                }
                scripts.append(write_script(topic, language="arabic"))
                scripts.append(write_script(topic, language="english"))

                # Move to processed/
                pf.rename(processed_dir / pf.name)
                print(f"[1/4] Processed and moved: {pf.name}")
            except Exception as e:
                print(f"[ERROR] Pending file {pf.name}: {e}")
    else:
        print("[1/4] No pending content — researching trending topics...")
        try:
            topics = research_topics(count=VIDEOS_PER_DAY)
        except Exception as e:
            send_message(f"Research failed: {e}")
            print(f"[ERROR] Research: {e}")
            return

        # ── STEP 2: Write Scripts ───────────────────────────────
        print("\n[2/4] Writing scripts...")
        try:
            scripts = write_scripts(topics)
        except Exception as e:
            send_message(f"Script writing failed: {e}")
            print(f"[ERROR] Scripts: {e}")
            return

    # ── STEP 3: Create Videos ───────────────────────────────
    print("\n[3/4] Creating videos...")
    video_queue = []

    for i, script_data in enumerate(scripts):
        video_id = f"{today}_video_{i+1}"
        try:
            video_path = create_video(script_data, video_id)
            if video_path and Path(video_path).exists():
                video_queue.append((video_path, script_data, video_id))
                stats["generated"] += 1
                print(f"  Video {i+1} ready: {video_path}")
            else:
                print(f"  [WARN] Video {i+1} assembly failed")
                stats["errors"] += 1
        except Exception as e:
            print(f"  [ERROR] Video {i+1}: {e}")
            stats["errors"] += 1

    if not video_queue:
        send_message("No videos were generated today. Check logs.")
        return

    # ── STEP 4: Notify + Approve + Publish ─────────────────
    print(f"\n[4/4] Sending {len(video_queue)} video(s) for approval...")

    for video_path, script_data, video_id in video_queue:
        try:
            decision = send_video_preview(video_path, script_data, video_id)

            if decision == "approve":
                print(f"  Approved: {video_id} — publishing...")
                results = publish_video(video_path, script_data)

                # Save publish log
                log_entry = {
                    "date": today,
                    "video_id": video_id,
                    "title": script_data["title"],
                    "niche": script_data["niche"],
                    "youtube":   results.get("youtube", ""),
                    "facebook":  results.get("facebook", ""),
                    "tiktok":    results.get("tiktok", ""),
                    "instagram": results.get("instagram", ""),
                }
                _save_log(log_entry)
                stats["posted"] += 1

                send_message(
                    f"Posted *{script_data['title']}*\n"
                    f"YouTube: {results.get('youtube', '-')}\n"
                    f"Facebook: {results.get('facebook', '-')}\n"
                    f"TikTok: {results.get('tiktok', '-')}\n"
                    f"Instagram: {results.get('instagram', '-')}"
                )
            else:
                print(f"  Skipped: {video_id}")
                stats["skipped"] += 1

        except Exception as e:
            print(f"  [ERROR] Publishing {video_id}: {e}")
            send_message(f"Error publishing {video_id}: {e}")
            stats["errors"] += 1

    # ── Daily Summary ───────────────────────────────────────
    send_daily_report(stats)
    print(f"\nDone. Generated: {stats['generated']} | Posted: {stats['posted']} | Skipped: {stats['skipped']}\n")


def _save_log(entry: dict):
    """Append to a simple JSON log file."""
    log_path = os.path.join("output", "publish_log.jsonl")
    Path("output").mkdir(exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    run_pipeline()
