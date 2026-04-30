# ============================================================
#  run_darkcrimed.py  —  Pipeline entry point for Dark Crime Decoded
#
#  Daily output (1 topic, 4 pieces):
#
#    OUTPUT 1 — English long-form (12-20 min) → auto YouTube upload
#    OUTPUT 2 — Arabic long-form  (12-20 min) → auto YouTube upload
#    OUTPUT 3 — English short (45-90s) → Telegram  [SHORT_MODE=script|cut]
#    OUTPUT 4 — Arabic short  (45-90s) → Telegram  [SHORT_MODE=script|cut]
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
from agent.script_agent   import write_script, translate_script, detect_part_number, generate_chapters, write_short_script
from agent.video_agent    import create_video, process_user_images_smart, load_part2_images, ensure_music_assets, cut_chapter_shorts, cut_best_short, load_all_content
from agent.notify_agent   import (
    send_message, send_for_manual_posting, send_daily_report,
    send_video_to_telegram, clear_telegram_queue,
    listen_for_content, send_arabic_script_preview, send_english_script_preview,
    check_telegram_for_script, check_telegram_for_images, check_telegram_for_videos,
    send_topic_confirmation,
)
from agent.publish_agent  import upload_to_youtube
from agents.content_agent import ingest_content_files

# SHORT_MODE controls how the daily short videos are generated.
# "script" (default) — TTS + full video assembly from the optimized short script.
# "cut"              — cut the best chapter clip from the finished long video.
# Falls back to "cut" automatically if short_script_en/ar are empty.
SHORT_MODE = os.getenv("SHORT_MODE", "script").lower()


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


# ═══════════════════════════════════════════════════════════════════════════════
#  Pipeline Utilities  (structured log · retry · stage timer · manifest dedup)
# ═══════════════════════════════════════════════════════════════════════════════

def _log(stage: str, msg: str, level: str = "INFO") -> None:
    """Timestamped structured log line."""
    ts  = datetime.datetime.now().strftime("%H:%M:%S")
    tag = {"WARN": "WARN", "ERROR": "ERR ", "OK": "OK  "}.get(level, "INFO")
    print(f"[{ts}][{tag}][{stage}] {msg}", flush=True)


def _with_retry(fn, *args, retries: int = 3, delay: float = 10.0,
                label: str = "", **kwargs):
    """Call fn(*args, **kwargs); retry up to `retries` times on transient failure."""
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == retries:
                raise
            wait = delay * attempt
            _log("Retry",
                 f"{label or fn.__name__} attempt {attempt}/{retries}: {exc} "
                 f"— retrying in {wait:.0f}s", "WARN")
            time.sleep(wait)


_PIPELINE_T0: float  = 0.0    # absolute pipeline start time
_STAGE_MARKS: list   = []     # [(name, timestamp), ...]  in insertion order


def _stage(name: str) -> None:
    """Record a named timing milestone and log it."""
    _STAGE_MARKS.append((name, time.time()))
    _log(name, "reached")


def _timing_report() -> str:
    """Return a multi-line stage-timing summary."""
    if not _STAGE_MARKS:
        return ""
    lines = ["", "── Stage Timings ─────────────────────────────────"]
    prev_t = _PIPELINE_T0
    for name, t in _STAGE_MARKS:
        dur = t - prev_t
        lines.append(f"  {name:<34} {dur / 60:5.1f} min")
        prev_t = t
    total = time.time() - _PIPELINE_T0
    lines.append(f"  {'TOTAL':<34} {total / 60:5.1f} min")
    lines.append("──────────────────────────────────────────────────")
    return "\n".join(lines)


def _load_existing_outputs(today: str, topic: str) -> tuple:
    """
    If today's manifest already has matching topic and both video files exist,
    return (en_path, ar_path) so the pipeline can skip regeneration on a rerun.
    """
    manifest_path = f"output/dark_crime/manifest_{today}.json"
    if not os.path.exists(manifest_path):
        return "", ""
    try:
        with open(manifest_path, encoding="utf-8") as _f:
            _d = json.load(_f)
        if _d.get("topic", "").lower().strip() != topic.lower().strip():
            return "", ""
        en = _d.get("videos", {}).get("en_long", "")
        ar = _d.get("videos", {}).get("ar_long", "")
        if en and ar and os.path.exists(en) and os.path.exists(ar):
            _log("Pipeline", f"Reusing existing videos for '{topic}'", "OK")
            return en, ar
    except Exception as _exc:
        _log("Pipeline", f"Manifest reuse check failed: {_exc}", "WARN")
    return "", ""


def run_pipeline():
    global _PIPELINE_T0, _STAGE_MARKS
    _PIPELINE_T0 = time.time()
    _STAGE_MARKS = []

    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*60}")
    print(f"  Dark Crime Decoded Pipeline — {today}")
    print(f"{'='*60}\n")
    _stage("Pipeline start")

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
            _log("Research", f"Researching: {topic_text}")
            try:
                research = _with_retry(research_series, topic_text, series_name,
                                       user_note=raw_input, retries=3, delay=12,
                                       label="research_series")
                if research is None:
                    _log("Research", "research_series returned None — aborting", "ERROR")
                    return
            except Exception as e:
                _log("Research", f"Web research failed for '{topic_text}': {e}", "WARN")
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
                auto_topics = _with_retry(research_topics, count=1,
                                          retries=3, delay=12, label="research_topics")
            except Exception as e:
                send_message(f"Research failed: {e}")
                _log("Research", f"research_topics failed: {e}", "ERROR")
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
                research_result = _with_retry(research_series, series,
                                              user_note=topic.get("user_note"),
                                              retries=3, delay=12, label="research_series")
                if research_result is None:
                    _log("Research", "research_series returned None — aborting", "ERROR")
                    return
                topic["research"] = research_result
            except Exception as e:
                _log("Research", f"Web research failed for '{series}': {e}", "WARN")
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

    _stage("Scripts EN done")

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
        _log("Scripts", "Arabic script done", "OK")
    except Exception as e:
        send_message(f"Arabic translation failed: {e}")
        _log("Scripts", f"Arabic translation failed: {e}", "ERROR")
        return

    # Generate short scripts: extract + rewrite strongest moment from long script
    try:
        _short_data = write_short_script(en_long)
        en_long["short_script_en"] = _short_data.get("short_script_en", "")
        ar_long["short_script_ar"] = _short_data.get("short_script_ar", "")
        _log("Scripts", "Short scripts done", "OK")
    except Exception as e:
        _log("Scripts", f"Short script generation failed (non-fatal): {e}", "WARN")

    _stage("Scripts AR done")

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
    _log("Telegram", "Scripts sent — continuing pipeline immediately.", "OK")
    _stage("Scripts sent to Telegram")

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

    # _gh_images is list[str]; _gh_videos is list[dict] (with duration/type pre-computed)
    _gh_img_dicts = [{"path": p, "tags": [], "caption": os.path.basename(p)} for p in _gh_images]
    _tg_imgs = list(user_images or [])
    _tg_vids = list(user_videos or [])
    user_images = _gh_img_dicts + _tg_imgs
    user_videos = _gh_videos + _tg_vids   # _gh_videos already dicts
    if _gh_images or _gh_videos:
        _gh_dur = sum(v.get("duration", 0) for v in _gh_videos)
        print(f"[Content] GitHub: {len(_gh_images)} images + {len(_gh_videos)} videos ({_gh_dur:.0f}s)")
        print(f"[Content] Telegram: {len(_tg_imgs)} images + {len(_tg_vids)} videos")
        print(f"[Content] Total: {len(user_images)} images + {len(user_videos)} videos")

    # ── STEP 4: Generate all 4 videos ─────────────────────────
    _log("VideoGen", "Starting video generation")
    _stage("Video gen start")

    # Skip regeneration if today's manifest already has valid files for this topic
    en_long_id, ar_long_id = "", ""
    _ex_en, _ex_ar = _load_existing_outputs(today, en_long.get("topic", ""))
    if _ex_en and _ex_ar:
        en_long_path, ar_long_path = _ex_en, _ex_ar
        stats["skipped"] += 2
        _log("VideoGen", "Reusing existing video files — skipping generation", "OK")
    else:
        # OUTPUT 1 — English long-form
        en_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_long"
        en_long_path = _make_video(en_long, en_long_id, stats, user_images=user_images, user_videos=user_videos)

        # OUTPUT 2 — Arabic long-form
        ar_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_long"
        ar_long_path = _make_video(ar_long, ar_long_id, stats, user_images=user_images, user_videos=user_videos)

    # Output 3: English short  ── script path or cut fallback
    en_chapter_shorts: list[dict] = []
    _en_short_script = en_long.get("short_script_en", "")
    if SHORT_MODE == "script" and _en_short_script:
        print("[Pipeline] Generating English short from short script (TTS → video)...")
        _en_short_data = {**en_long, "script": _en_short_script}
        _en_short_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_short"
        _en_short_path = _make_video(_en_short_data, _en_short_id, stats)
        if _en_short_path:
            en_chapter_shorts = [{
                "path":        _en_short_path,
                "title":       en_long.get("title", ""),
                "label":       "Best Short — TikTok + Instagram + YouTube Shorts",
                "chapter_idx": 1,
            }]
    else:
        _reason = "SHORT_MODE=cut" if _en_short_script else "short_script_en missing"
        print(f"[Pipeline] Cutting best English short from long video ({_reason})...")
        if en_long_path and os.path.exists(en_long_path):
            en_chapter_shorts = cut_best_short(en_long_path, en_long)

    # Output 4: Arabic short  ── script path or cut fallback
    ar_chapter_shorts: list[dict] = []
    _ar_short_script = ar_long.get("short_script_ar", "")
    if SHORT_MODE == "script" and _ar_short_script:
        print("[Pipeline] Generating Arabic short from short script (TTS → video)...")
        _ar_short_data = {**ar_long, "script": _ar_short_script}
        _ar_short_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_short"
        _ar_short_path = _make_video(_ar_short_data, _ar_short_id, stats)
        if _ar_short_path:
            ar_chapter_shorts = [{
                "path":        _ar_short_path,
                "title":       ar_long.get("title", ""),
                "label":       "Best Short — TikTok + Instagram + YouTube Shorts",
                "chapter_idx": 1,
            }]
    else:
        _reason = "SHORT_MODE=cut" if _ar_short_script else "short_script_ar missing"
        print(f"[Pipeline] Cutting best Arabic short from long video ({_reason})...")
        if ar_long_path and os.path.exists(ar_long_path):
            ar_chapter_shorts = cut_best_short(ar_long_path, ar_long)

    _stage("Videos + shorts done")

    # Clear user images + videos so they don't bleed into the next run
    import shutil as _shutil
    for _clear_dir in ("output/user_images", "output/user_videos"):
        try:
            if os.path.exists(_clear_dir):
                _shutil.rmtree(_clear_dir)
            os.makedirs(_clear_dir, exist_ok=True)
        except Exception as _ce:
            _log("Cleanup", f"Could not reset {_clear_dir}: {_ce}", "WARN")
    _log("Cleanup", "User media dirs reset for next run", "OK")

    # ── STEP 5: Upload long videos to YouTube, then send shorts to Telegram ──
    _log("Publish", "Starting publishing step")
    _stage("Publish start")

    # Retry any failed uploads from previous pipeline runs
    _retry_failed_uploads()

    # Build GitHub Actions artifact URL for failure notifications
    _run_id   = os.getenv("GITHUB_RUN_ID", "")
    _repo     = os.getenv("GITHUB_REPOSITORY", "abdotaj/AI_Content_Pipeline")
    _artifact_url = f"https://github.com/{_repo}/actions/runs/{_run_id}" if _run_id else ""

    yt_en_url = None
    if en_long_path:
        try:
            _log("Publish", "Uploading English long to YouTube...")
            yt_en_url = _with_retry(upload_to_youtube, en_long_path, en_long,
                                    token_file=YOUTUBE_TOKEN_FILE_EN,
                                    retries=3, delay=30, label="YT EN upload")
            send_message(
                f"✅ English Video Published on YouTube!\n\n"
                f"🎬 {en_long.get('title', '')}\n"
                f"🔗 {yt_en_url}\n\n"
                f"Duration: {get_duration(en_long_path)}"
            )
            _log("Publish", f"English YouTube: {yt_en_url}", "OK")
        except Exception as e:
            _log("Publish", f"English YouTube upload failed: {e}", "ERROR")
            _fail_msg = f"❌ English YouTube upload failed: {e}"
            if _artifact_url:
                _fail_msg += f"\n\nDownload video from GitHub artifact:\n{_artifact_url}"
            send_message(_fail_msg)
            stats["errors"] += 1

    yt_ar_url = None
    if ar_long_path:
        try:
            _log("Publish", "Uploading Arabic long to YouTube...")
            yt_ar_url = _with_retry(upload_to_youtube, ar_long_path, ar_long,
                                    token_file=YOUTUBE_TOKEN_FILE_AR,
                                    retries=3, delay=30, label="YT AR upload")
            send_message(
                f"✅ تم نشر الفيديو العربي على يوتيوب!\n\n"
                f"🎬 {ar_long.get('title', '')}\n"
                f"🔗 {yt_ar_url}\n\n"
                f"المدة: {get_duration(ar_long_path)}"
            )
            _log("Publish", f"Arabic YouTube: {yt_ar_url}", "OK")
        except Exception as e:
            _log("Publish", f"Arabic YouTube upload failed: {e}", "ERROR")
            _fail_msg = f"❌ Arabic YouTube upload failed: {e}"
            if _artifact_url:
                _fail_msg += f"\n\nDownload video from GitHub artifact:\n{_artifact_url}"
            send_message(_fail_msg)
            stats["errors"] += 1

    # Send best English short to Telegram (1 video, reliable)
    if en_chapter_shorts:
        short = en_chapter_shorts[0]
        try:
            caption = (
                f"MANUAL POST NEEDED\n\n"
                f"{short['title']}\n"
                f"Post to: {short['label']}\n\n"
                f"Topic: {en_long.get('title', '')}\n"
                f"{en_long.get('hashtags', '')}"
            )
            _with_retry(send_video_to_telegram, short["path"], caption,
                        "EN Best Short",
                        retries=3, delay=10, label="TG EN Best Short")
        except Exception as e:
            _log("Telegram", f"EN best short send failed: {e}", "WARN")

    # Send best Arabic short to Telegram (1 video, reliable)
    if ar_chapter_shorts:
        short = ar_chapter_shorts[0]
        try:
            caption = (
                f"MANUAL POST NEEDED\n\n"
                f"{short['title']}\n"
                f"Post to: {short['label']}\n\n"
                f"Topic: {ar_long.get('title', '')}\n"
                f"{ar_long.get('hashtags', '')}"
            )
            _with_retry(send_video_to_telegram, short["path"], caption,
                        "AR Best Short",
                        retries=3, delay=10, label="TG AR Best Short")
        except Exception as e:
            _log("Telegram", f"AR best short send failed: {e}", "WARN")

    # ── Save manifest (2 long videos + shorts summary) ────────
    _save_manifest(
        today,
        en_long, ar_long,
        en_long_path, ar_long_path,
        en_chapter_shorts, ar_chapter_shorts,
        yt_en_url, yt_ar_url,
    )
    _stage("Publish done")

    # ── Daily summary ──────────────────────────────────────────
    _total_shorts = len(en_chapter_shorts) + len(ar_chapter_shorts)
    _total_elapsed = (time.time() - _PIPELINE_T0) / 60
    _status_en = f"✅ {yt_en_url}" if yt_en_url else "❌ Upload failed"
    _status_ar = f"✅ {yt_ar_url}" if yt_ar_url else "❌ Upload failed"
    send_message(
        f"📊 Daily Report — Dark Crime Decoded\n\n"
        f"✅ Generated: 2 long + {_total_shorts} shorts (1 EN + 1 AR best chapters)\n"
        f"⏱ Total time: {_total_elapsed:.0f} min\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎬 English Long → YouTube\n"
        f"{_status_en}\n\n"
        f"🎬 Arabic Long → YouTube\n"
        f"{_status_ar}\n\n"
        f"📱 {len(en_chapter_shorts)} EN Best Short → Telegram ✅\n"
        f"📱 {len(ar_chapter_shorts)} AR Best Short → Telegram ✅\n"
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

    # ── Final console summary ──────────────────────────────────
    _result = "SUCCESS" if stats["errors"] == 0 else f"PARTIAL ({stats['errors']} error(s))"
    print(_timing_report())
    print(f"\n{'='*60}")
    print(f"  Pipeline {_result} — {today}")
    print(f"  Generated: {stats['generated']} | Skipped: {stats['skipped']} "
          f"| Posted: {stats['posted']} | Errors: {stats['errors']}")
    print(f"  YouTube EN: {yt_en_url or 'FAILED'}")
    print(f"  YouTube AR: {yt_ar_url or 'FAILED'}")
    print(f"  Shorts sent: {_total_shorts} (1 EN + 1 AR best chapters)")
    print(f"{'='*60}\n")


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
            url = _with_retry(upload_to_youtube, item["path"], item["script"],
                              token_file=item["token"],
                              retries=3, delay=30, label=f"Recovery {label}")
            if url:
                _log("Recovery", f"{label} recovered: {url}", "OK")
                send_message(f"✅ [Recovery] {label} video uploaded: {url}")
                try:
                    with open(item["manifest"], encoding="utf-8") as f:
                        mdata = json.load(f)
                    key    = "en_long_uploaded" if "en" in item["type"] else "ar_long_uploaded"
                    yt_key = "en"               if "en" in item["type"] else "ar"
                    mdata["status"][key]          = True
                    mdata["youtube_urls"][yt_key] = url
                    _tmp = item["manifest"] + ".tmp"
                    with open(_tmp, "w", encoding="utf-8") as f:
                        json.dump(mdata, f, ensure_ascii=False, indent=2)
                    os.replace(_tmp, item["manifest"])
                except Exception as e2:
                    _log("Recovery", f"Could not update manifest after recovery: {e2}", "WARN")
        except Exception as e:
            _log("Recovery", f"{label} retry failed: {e}", "WARN")


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
    _tmp = manifest_path + ".tmp"
    try:
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(_tmp, manifest_path)
    except Exception as _me:
        _log("Manifest", f"Atomic write failed: {_me} — falling back to direct write", "WARN")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    _log("Manifest", f"Saved: {manifest_path}", "OK")
    return manifest_path


def _save_log(entry: dict):
    log_path = os.path.join("output", "dark_crime", "publish_log.jsonl")
    Path("output/dark_crime").mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run_pipeline()
