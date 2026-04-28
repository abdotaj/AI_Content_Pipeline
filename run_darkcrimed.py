# ============================================================
#  run_darkcrimed.py  —  Pipeline entry point for Dark Crime Decoded
#
#  Daily output (1 topic, 12 pieces):
#
#    OUTPUT 1  — English long-form (12-20 min) → auto YouTube upload
#    OUTPUT 2  — Arabic long-form  (12-20 min) → auto YouTube upload
#    OUTPUTS 3-7  — 5 English chapter shorts (55-90s each) → Telegram
#    OUTPUTS 8-12 — 5 Arabic  chapter shorts (55-90s each) → Telegram
# ============================================================
import os
import sys
import json
import uuid
import time
import glob
import datetime
import requests
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
    FINAL_DIR, CONTENT_DIR, YOUTUBE_TOKEN_FILE_EN, YOUTUBE_TOKEN_FILE_AR,
    SHORT_VIDEO_DURATION,
)

# Token files are written by daily.yml steps before pipeline runs (CI)
# Local: use existing youtube_token_darkcrimed_en/ar.json files

from agent.research_agent import research_topics, research_series, mark_covered, is_fictional, _detect_show_topic, _fetch_show_cast_from_wikipedia
from agent.script_agent   import write_script, translate_script, detect_part_number, generate_chapters
from agent.video_agent    import create_video, process_user_images_smart, load_part2_images, ensure_music_assets, cut_chapter_shorts, load_all_content
from agent.notify_agent   import (
    send_message, send_for_manual_posting, send_daily_report,
    send_video_to_telegram, clear_telegram_queue,
    listen_for_content, send_arabic_script_preview, send_english_script_preview,
    check_telegram_for_script, check_telegram_for_images, check_telegram_for_videos,
    send_topic_confirmation,
)
from agent.publish_agent  import upload_to_youtube
from agents.content_agent import ingest_content_files


def _already_ran_today() -> bool:
    """Return True if a manifest for today's date already exists."""
    today = datetime.date.today().isoformat()
    # Fast path: manifest file named with today's date
    if glob.glob(f"output/dark_crime/manifest_{today}.json"):
        return True
    # Slow path: scan all manifests for a matching date field
    for m in glob.glob("output/dark_crime/manifest_*.json"):
        try:
            with open(m) as f:
                data = json.load(f)
            if data.get("date") == today:
                return True
        except Exception:
            pass
    return False


def check_24h_cooldown() -> bool:
    """Return True if pipeline should run, False if last run was < 24 hours ago."""
    manifests = glob.glob("output/dark_crime/manifest_*.json")

    if not manifests:
        print("[Pipeline] No previous runs found — starting fresh")
        return True

    latest = max(manifests, key=os.path.getmtime)

    try:
        with open(latest) as f:
            data = json.load(f)

        # Use saved timestamp; fall back to file mtime for old manifests
        last_run = data.get("timestamp") or os.path.getmtime(latest)
        elapsed = time.time() - last_run
        elapsed_hours = elapsed / 3600

        print(f"[Pipeline] Last run: {elapsed_hours:.1f} hours ago")

        if elapsed_hours < 24:
            remaining = 24 - elapsed_hours
            print(f"[Pipeline] Too soon — {remaining:.1f} hours remaining")
            send_message(
                f"\u23f0 Pipeline Cooldown Active\n\n"
                f"Last run: {elapsed_hours:.1f} hours ago\n"
                f"Next run available in: {remaining:.1f} hours\n\n"
                f'To force run anyway send: "force run"'
            )
            return False

        print("[Pipeline] Cooldown passed — ready to run")
        return True

    except Exception as e:
        print(f"[Pipeline] Cooldown check error: {e}")
        return True  # Run anyway if check fails


def check_force_run() -> bool:
    """Return True if user sent 'force run' to Telegram in the last 5 minutes."""
    from config_darkcrimed import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    cutoff = time.time() - 300  # 5 minutes

    try:
        r = requests.get(f"{base_url}/getUpdates", params={"limit": 20}, timeout=10)
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"[Pipeline] check_force_run error: {e}")
        return False

    for upd in updates:
        msg = upd.get("message", {})
        if str(msg.get("chat", {}).get("id", "")) != str(TELEGRAM_CHAT_ID):
            continue
        if msg.get("date", 0) < cutoff:
            continue
        if "force run" in msg.get("text", "").lower():
            print("[Pipeline] Force run requested by user")
            return True

    return False


def run_pipeline():
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*50}")
    print(f"  Dark Crime Decoded Pipeline — {today}")
    print(f"{'='*50}\n")

    # ── Date-based cooldown (scheduled runs only) ──────────────
    # workflow_dispatch and local runs always proceed regardless.
    _event = os.getenv("GITHUB_EVENT_NAME", "")
    if _event == "schedule":
        if _already_ran_today():
            print("[Pipeline] Already ran today — exiting")
            sys.exit(0)
        print("[Pipeline] Scheduled run — no run today yet, proceeding")
    else:
        print(f"[Pipeline] Trigger: '{_event or 'local'}' — cooldown check skipped")

    # ── Ensure music assets are downloaded ────────────────────
    ensure_music_assets()

    # ── Cooldown guard ─────────────────────────────────────────
    if not check_24h_cooldown():
        if not check_force_run():
            print("[Pipeline] Skipping — cooldown active")
            return
        print("[Pipeline] Cooldown bypassed by user")

    # ── STEP 1: Topic + images ────────────────────────────────
    pipeline_start_time = time.time()

    # Priority 1: content/dark_crime/ JSON files (skip Telegram flow)
    ingested = ingest_content_files(content_dir=CONTENT_DIR)

    topic = None
    user_images: list = []
    user_videos: list = []
    _part_number:       int | None = None
    _series_name_for_filter: str | None = None

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
        time.sleep(60)

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

            _part_number = detect_part_number(raw_input)
            _series_name_for_filter = series_name
            if _part_number:
                print(f"[Pipeline] Part {_part_number} detected in user note")

            # ── 1D: Ask for photos now that topic is confirmed ────────────────
            # Quick show_characters lookup (uses hardcoded map — no API call for known shows)
            _is_show, _show_key = _detect_show_topic(topic_text)
            _quick_chars: list = []
            if _is_show:
                _quick_chars = _fetch_show_cast_from_wikipedia(series_name or _show_key or topic_text)
            send_topic_confirmation(
                topic_text=topic_text,
                series_name=series_name,
                show_characters=_quick_chars,
                is_show_topic=_is_show,
            )
            print("[1/5] Waiting 3 minutes for photos...")
            time.sleep(180)

            # ── 1E: Collect images + videos sent AFTER pipeline start ────────
            user_images = check_telegram_for_images(after_timestamp=pipeline_start_time)
            user_videos = check_telegram_for_videos(after_timestamp=pipeline_start_time)
            if user_videos:
                print(f"[1/5] Found {len(user_videos)} video(s) from Telegram")
            if user_images:
                print(f"[1/5] Found {len(user_images)} image(s) for '{topic_text}' — checking relevance...")
                _use_now, _save_later, _ignored = process_user_images_smart(
                    user_images,
                    topic=topic_text,
                    series_name=series_name,
                    part_number=_part_number,
                )
                user_images = _use_now
                send_message(
                    f"📸 Image Check Complete for: {topic_text}\n\n"
                    f"✅ Using now: {len(_use_now)} images\n"
                    f"📦 Saved for Part 2: {len(_save_later)} images\n"
                    f"❌ Not relevant: {len(_ignored)} images"
                )
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
            if user_images:
                _auto_topic  = topic.get("topic", "")
                _auto_series = topic.get("series_name") or topic.get("niche", "")
                _use_now, _, _ = process_user_images_smart(
                    user_images, topic=_auto_topic,
                    series_name=_auto_series, part_number=None,
                )
                user_images = _use_now

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
        ar_long = translate_script(en_long)
        # Replace English chapter labels with Arabic equivalents, preserving angle_title
        ar_word_count = len(ar_long.get("script", "").split())
        if ar_word_count > 0:
            ar_long["chapters"] = generate_chapters(
                ar_word_count,
                language="arabic",
                angle_title=en_long.get("angle_title", ""),
            )
        # Carry angle fields through to Arabic
        ar_long["angle_title"] = en_long.get("angle_title", "")
        ar_long["angle_hook"]  = en_long.get("angle_hook", "")
        print("  [2/5] Arabic long script done")
    except Exception as e:
        send_message(f"Arabic translation failed: {e}")
        print(f"[ERROR] Arabic translation: {e}")
        return

    # ── STEP 3: Send scripts to Telegram for review (non-blocking) ────────────
    print("\n[3/5] Sending scripts to Telegram for review...")
    for fn, script, label in [
        (send_arabic_script_preview,  ar_long,  "Arabic LONG script (10-14 min)"),
        (send_english_script_preview, en_long,  "English LONG script (10-14 min)"),
    ]:
        try:
            fn(script, label=label)
        except Exception as e:
            print(f"  [WARN] Script preview failed ({label}): {e}")
    print("  Scripts sent — continuing pipeline immediately.")

    # ── Load saved Part 2 images if this is a Part 2 run ──────
    _part_num_final = en_long.get("part_number")
    if _part_num_final == 2:
        _p2_paths = load_part2_images(en_long.get("topic", ""))
        if _p2_paths:
            _p2_dicts = [{"path": p, "tags": ["portrait", "real"]} for p in _p2_paths]
            user_images = _p2_dicts + list(user_images)
            print(f"[Pipeline] Added {len(_p2_paths)} saved Part 2 images")
            send_message(f"[Pipeline] Loaded {len(_p2_paths)} saved images for Part 2")

    # ── Load GitHub content library for this topic ────────────
    _gh_images, _gh_videos, _gh_music_long, _gh_music_short = load_all_content(
        en_long.get("topic", "")
    )
    if _gh_music_long:
        import shutil as _shutil
        _shutil.copy(_gh_music_long, "assets/music/documentary_long.mp3")
        print("[GitHub] Custom music applied for long video")
    if _gh_music_short:
        import shutil as _shutil
        _shutil.copy(_gh_music_short, "assets/music/documentary_short.mp3")
        print("[GitHub] Custom music applied for short video")

    _gh_img_dicts = [{"path": p, "tags": [], "caption": os.path.basename(p)} for p in _gh_images]
    _gh_vid_dicts = [{"path": p, "tags": [], "caption": os.path.basename(p)} for p in _gh_videos]
    _tg_imgs = list(user_images or [])
    _tg_vids = list(user_videos or [])
    user_images = _gh_img_dicts + _tg_imgs
    user_videos = _gh_vid_dicts + _tg_vids
    if _gh_images or _gh_videos:
        print(f"[Content] Total: {len(user_images)} images + {len(user_videos)} videos")
        print(f"[Content] GitHub: {len(_gh_images)} images + {len(_gh_videos)} videos")
        print(f"[Content] Telegram: {len(_tg_imgs)} images + {len(_tg_vids)} videos")

    # ── STEP 4: Generate all 4 videos ─────────────────────────
    print("\n[4/5] Generating videos...")

    # OUTPUT 1 — English long-form
    en_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_long"
    en_long_path = _make_video(en_long, en_long_id, stats, user_images=user_images, user_videos=user_videos)

    # OUTPUT 2 — Arabic long-form
    ar_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_long"
    ar_long_path = _make_video(ar_long, ar_long_id, stats, user_images=user_images, user_videos=user_videos)

    # Outputs 3-7: cut 5 chapter shorts from English long video
    en_chapter_shorts: list[dict] = []
    if en_long_path and os.path.exists(en_long_path):
        print("[Pipeline] Cutting 5 English chapter shorts...")
        en_chapter_shorts = cut_chapter_shorts(en_long_path, en_long)

    # Outputs 8-12: cut 5 chapter shorts from Arabic long video
    ar_chapter_shorts: list[dict] = []
    if ar_long_path and os.path.exists(ar_long_path):
        print("[Pipeline] Cutting 5 Arabic chapter shorts...")
        ar_chapter_shorts = cut_chapter_shorts(ar_long_path, ar_long)

    # Clear user images + videos so they don't bleed into the next run
    import shutil as _shutil
    for _clear_dir in ("output/user_images", "output/user_videos"):
        if os.path.exists(_clear_dir):
            _shutil.rmtree(_clear_dir)
            os.makedirs(_clear_dir)
    print("[Pipeline] User images + videos cleared for next run")

    # ── STEP 5: Upload long videos to YouTube, then send shorts to Telegram ──
    print("\n[5/5] Publishing videos...")

    # Retry any failed uploads from previous pipeline runs
    _retry_failed_uploads()

    # Build GitHub Actions artifact URL for failure notifications
    _run_id   = os.getenv("GITHUB_RUN_ID", "")
    _repo     = os.getenv("GITHUB_REPOSITORY", "abdotaj/AI_Content_Pipeline")
    _artifact_url = f"https://github.com/{_repo}/actions/runs/{_run_id}" if _run_id else ""

    yt_en_url = None
    if en_long_path:
        try:
            print("[Publish] Uploading English long to YouTube...")
            yt_en_url = upload_to_youtube(en_long_path, en_long, token_file=YOUTUBE_TOKEN_FILE_EN)
            send_message(
                f"✅ English Video Published on YouTube!\n\n"
                f"🎬 {en_long.get('title', '')}\n"
                f"🔗 {yt_en_url}\n\n"
                f"Duration: {get_duration(en_long_path)}"
            )
            print(f"  [Publish] English YouTube URL: {yt_en_url}")
        except Exception as e:
            print(f"  [ERROR] English YouTube upload failed: {e}")
            _fail_msg = f"❌ English YouTube upload failed: {e}"
            if _artifact_url:
                _fail_msg += f"\n\nDownload video from GitHub artifact:\n{_artifact_url}"
            send_message(_fail_msg)

    yt_ar_url = None
    if ar_long_path:
        try:
            print("[Publish] Uploading Arabic long to YouTube...")
            yt_ar_url = upload_to_youtube(ar_long_path, ar_long, token_file=YOUTUBE_TOKEN_FILE_AR)
            send_message(
                f"✅ تم نشر الفيديو العربي على يوتيوب!\n\n"
                f"🎬 {ar_long.get('title', '')}\n"
                f"🔗 {yt_ar_url}\n\n"
                f"المدة: {get_duration(ar_long_path)}"
            )
            print(f"  [Publish] Arabic YouTube URL: {yt_ar_url}")
        except Exception as e:
            print(f"  [ERROR] Arabic YouTube upload failed: {e}")
            _fail_msg = f"❌ Arabic YouTube upload failed: {e}"
            if _artifact_url:
                _fail_msg += f"\n\nDownload video from GitHub artifact:\n{_artifact_url}"
            send_message(_fail_msg)

    # Send 5 English chapter shorts to Telegram
    for short in en_chapter_shorts:
        try:
            caption = (
                f"MANUAL POST NEEDED\n\n"
                f"Chapter {short['chapter_idx']}: {short['title']}\n"
                f"Post to: {short['label']}\n\n"
                f"Topic: {en_long.get('title', '')}\n"
                f"{en_long.get('hashtags', '')}"
            )
            send_video_to_telegram(short["path"], caption, f"EN Short Ch{short['chapter_idx']}")
        except Exception as e:
            print(f"  [WARN] Telegram EN short Ch{short['chapter_idx']} send failed: {e}")

    # Send 5 Arabic chapter shorts to Telegram
    for short in ar_chapter_shorts:
        try:
            caption = (
                f"MANUAL POST NEEDED\n\n"
                f"Chapter {short['chapter_idx']}: {short['title']}\n"
                f"Post to: {short['label']}\n\n"
                f"Topic: {ar_long.get('title', '')}\n"
                f"{ar_long.get('hashtags', '')}"
            )
            send_video_to_telegram(short["path"], caption, f"AR Short Ch{short['chapter_idx']}")
        except Exception as e:
            print(f"  [WARN] Telegram AR short Ch{short['chapter_idx']} send failed: {e}")

    # ── Save manifest (2 long videos + shorts summary) ────────
    _save_manifest(
        today,
        en_long, ar_long,
        en_long_path, ar_long_path,
        en_chapter_shorts, ar_chapter_shorts,
        yt_en_url, yt_ar_url,
    )

    # ── Daily summary ──────────────────────────────────────────
    _total_shorts = len(en_chapter_shorts) + len(ar_chapter_shorts)
    send_message(
        f"📊 Daily Report — Dark Crime Decoded\n\n"
        f"✅ Generated: 2 long + {_total_shorts} shorts ({_total_shorts // 2} EN + {_total_shorts // 2} AR)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎬 English Long → YouTube\n"
        f"{yt_en_url or '❌ Upload failed'}\n\n"
        f"🎬 Arabic Long → YouTube\n"
        f"{yt_ar_url or '❌ Upload failed'}\n\n"
        f"📱 {len(en_chapter_shorts)} EN Chapter Shorts → Telegram ✅\n"
        f"📱 {len(ar_chapter_shorts)} AR Chapter Shorts → Telegram ✅\n"
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
        "date":           today,
        "channel":        "dark_crime",
        "en_long_id":     en_long_id,
        "ar_long_id":     ar_long_id,
        "en_shorts":      len(en_chapter_shorts),
        "ar_shorts":      len(ar_chapter_shorts),
        "title":          en_long.get("title", ""),
        "niche":          en_long.get("niche", ""),
        "youtube_en":     yt_en_url or "",
        "youtube_ar":     yt_ar_url or "",
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


def _make_video(script_data: dict, video_id: str, stats: dict, user_images: list | None = None, user_videos: list | None = None) -> str:
    """Create a video using ElevenLabs + Pollinations, update stats, return path."""
    try:
        path = create_video(script_data, video_id, user_images=user_images, user_videos=user_videos)
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


def check_failed_uploads() -> list:
    """Return list of failed YouTube uploads from previous runs where the video still exists."""
    import glob
    failed = []
    for m in sorted(glob.glob("output/dark_crime/manifest_*.json")):
        try:
            with open(m) as f:
                data = json.load(f)
            status  = data.get("status", {})
            videos  = data.get("videos", {})
            scripts = data.get("script_data", {})

            if not status.get("en_long_uploaded"):
                path = videos.get("en_long", "")
                if path and os.path.exists(path):
                    failed.append({
                        "path":     path,
                        "script":   scripts.get("en_long", {}),
                        "token":    "youtube_token_darkcrimed_en.json",
                        "type":     "en_long",
                        "manifest": m,
                    })

            if not status.get("ar_long_uploaded"):
                path = videos.get("ar_long", "")
                if path and os.path.exists(path):
                    failed.append({
                        "path":     path,
                        "script":   scripts.get("ar_long", {}),
                        "token":    "youtube_token_darkcrimed_ar.json",
                        "type":     "ar_long",
                        "manifest": m,
                    })
        except Exception as e:
            print(f"  [WARN] Failed to read manifest {m}: {e}")
    return failed


def _retry_failed_uploads():
    """Retry YouTube uploads that failed in previous pipeline runs."""
    failed = check_failed_uploads()
    if not failed:
        return

    print(f"[Recovery] {len(failed)} failed upload(s) from previous runs — retrying...")
    send_message(f"[Recovery] Retrying {len(failed)} failed upload(s) from previous runs...")

    for item in failed:
        label = "English" if "en" in item["type"] else "Arabic"
        try:
            url = upload_to_youtube(item["path"], item["script"], token_file=item["token"])
            if url:
                print(f"  [Recovery] {label} recovered: {url}")
                send_message(f"✅ [Recovery] {label} video uploaded: {url}")
                try:
                    with open(item["manifest"]) as f:
                        mdata = json.load(f)
                    key    = "en_long_uploaded" if "en" in item["type"] else "ar_long_uploaded"
                    yt_key = "en"               if "en" in item["type"] else "ar"
                    mdata["status"][key]         = True
                    mdata["youtube_urls"][yt_key] = url
                    with open(item["manifest"], "w") as f:
                        json.dump(mdata, f, ensure_ascii=False, indent=2)
                except Exception as e2:
                    print(f"  [WARN] Could not update manifest after recovery: {e2}")
        except Exception as e:
            print(f"  [Recovery] {label} retry failed: {e}")


def _save_manifest(today, en_long, ar_long,
                   en_long_path, ar_long_path,
                   en_chapter_shorts, ar_chapter_shorts,
                   yt_en_url, yt_ar_url) -> str:
    """Save a JSON manifest recording long video paths, shorts, and upload status."""
    manifest = {
        "timestamp": time.time(),
        "date":  today,
        "topic": en_long.get("topic", ""),
        "videos": {
            "en_long":        en_long_path,
            "ar_long":        ar_long_path,
            "en_shorts":      [s["path"] for s in en_chapter_shorts],
            "ar_shorts":      [s["path"] for s in ar_chapter_shorts],
        },
        "scripts": {
            "en_long_title": en_long.get("title", ""),
            "ar_long_title": ar_long.get("title", ""),
        },
        "script_data": {
            "en_long": {k: en_long.get(k, "") for k in ("title", "description", "tags", "language", "niche")},
            "ar_long": {k: ar_long.get(k, "") for k in ("title", "description", "tags", "language", "niche")},
        },
        "youtube_urls": {
            "en": yt_en_url or "",
            "ar": yt_ar_url or "",
        },
        "telegram_sent": {
            "en_shorts": len(en_chapter_shorts),
            "ar_shorts": len(ar_chapter_shorts),
        },
        "status": {
            "en_long_uploaded": bool(yt_en_url),
            "ar_long_uploaded": bool(yt_ar_url),
            "en_shorts_sent":   len(en_chapter_shorts),
            "ar_shorts_sent":   len(ar_chapter_shorts),
        },
    }
    Path("output/dark_crime").mkdir(parents=True, exist_ok=True)
    manifest_path = f"output/dark_crime/manifest_{today}.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[Manifest] Saved: {manifest_path}")
    return manifest_path


def _save_log(entry: dict):
    log_path = os.path.join("output", "dark_crime", "publish_log.jsonl")
    Path("output/dark_crime").mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run_pipeline()
