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


def _topic_slug(topic: str, max_len: int = 35) -> str:
    """Convert a topic string to a clean filename-safe slug."""
    import re as _re
    s = _re.sub(r'[^a-zA-Z0-9\s]', '', (topic or "video").lower()).strip()
    s = _re.sub(r'\s+', '_', s)
    return (s[:max_len].rstrip('_')) or "video"
import datetime
import traceback
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
from agent.script_agent   import (
    write_script, translate_script, detect_part_number, generate_chapters,
    write_short_script, generate_cinematic_shorts,
    write_arabic_script, write_arabic_short,   # language-isolated Arabic pipeline
)
from agent.video_agent    import create_video, process_user_images_smart, load_part2_images, ensure_music_assets, cut_chapter_shorts, cut_best_short, load_all_content, find_content_folder
from agent.notify_agent   import (
    send_message, send_for_manual_posting, send_daily_report,
    send_video_to_telegram, clear_telegram_queue,
    listen_for_content, send_arabic_script_preview, send_english_script_preview,
    check_telegram_for_script, check_telegram_for_images, check_telegram_for_videos,
    send_topic_confirmation,
)
from agent.publish_agent  import upload_to_youtube
from agents.content_agent import ingest_content_files
from pipelines.approval import wait_for_approval

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
        if (_d.get("topic") or "").lower().strip() != (topic or "").lower().strip():
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

    # Priority 2: topic_inject.json — created by create_topic.py, consumed once
    _inject_file = os.path.join(os.path.dirname(__file__), "topic_inject.json")
    if not ingested and os.path.exists(_inject_file):
        try:
            with open(_inject_file, encoding="utf-8") as _f:
                _inject = json.load(_f)
            os.remove(_inject_file)
            _inject_topic_text = _inject.get("topic", "").strip()
            if _inject_topic_text:
                print(f"[1/5] topic_inject.json consumed: '{_inject_topic_text}'")
                from agent.script_agent import get_series_for_person as _gsfp_inj
                _inj_si = _gsfp_inj(_inject_topic_text)
                _inj_series = (_inj_si[0] if _inj_si else None) or _inject.get("show") or None
                _log("Research", f"Inject topic: {_inject_topic_text}")
                try:
                    _inj_res = _with_retry(research_series, _inject_topic_text, _inj_series,
                                           user_note=_inject.get("note", ""), retries=3, delay=12,
                                           label="research_series")
                    if _inj_res is None:
                        _inj_res = {}
                except Exception as _inj_e:
                    _log("Research", f"research_series failed (inject): {_inj_e}", "WARN")
                    _inj_res = {}
                if _inj_res:
                    _inj_res["real_person"] = _inject_topic_text
                    _inj_res["series_name"] = _inj_series or _inject_topic_text
                topic = {
                    "topic":         _inject_topic_text,
                    "niche":         f"Real story behind {_inj_series or _inject_topic_text}",
                    "angle":         "",
                    "keywords":      [_inject_topic_text],
                    "search_query":  _inject_topic_text,
                    "series_name":   _inj_series,
                    "research":      _inj_res,
                    "manual_topic":  True,
                    "force_rewrite": bool(_inject.get("force_rewrite", False)),
                }
                send_message(f"[Pipeline] Inject topic: {_inject_topic_text}\nSkipping Telegram wait.")
        except Exception as _ie:
            print(f"[1/5] topic_inject.json read error (ignored): {_ie}")

    if ingested:
        print("[1/5] Using script from content files.")
        en_long = next((s for s in ingested if s.get("language") == "english"), ingested[0])

    elif not topic:
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
                "manual_topic": True,
            }

            # ── Risk classification — manual topics always allowed ────────────
            try:
                from agents.topic_risk import classify_topic_risk, log_risk
                _risk_manual = classify_topic_risk(topic_text, is_manual=True)
                log_risk(topic_text, _risk_manual)
                topic["risk_info"] = _risk_manual
                if _risk_manual["editorial_mode"]:
                    send_message(
                        f"⚠️ Sensitive topic detected: {topic_text}\n\n"
                        f"Risk level: {_risk_manual['risk_level']}\n"
                        f"Editorial-assist mode is ACTIVE — narration will use evidential framing.\n"
                        f"Creator retains full editorial control."
                    )
            except Exception as _risk_e:
                print(f"[RISK] Classification failed (non-fatal): {_risk_e}")
                topic["risk_info"] = {}

        else:
            # No topic sent — strict manual-only policy: abort instead of auto-select
            print("[Pipeline] No topic received — aborting (manual topic required)")
            send_message(
                "⛔ No topic received.\n\n"
                "Send your topic name (e.g. 'Jeffrey Epstein') within 60 seconds of starting.\n"
                "Or use topic_inject.json to pre-set a topic.\n\n"
                "Pipeline stopped."
            )
            _log("Research", "No topic received — pipeline aborted (manual required)", "WARN")
            return
            topic_text  = topic.get("topic", "")
            topic_niche = topic.get("niche", "")

            # ── Belt-and-suspenders risk check (research_agent already filters) ─
            try:
                from agents.topic_risk import classify_topic_risk, log_risk
                _risk_auto = classify_topic_risk(topic_text, is_manual=False)
                log_risk(topic_text, _risk_auto)
                if _risk_auto.get("manual_confirmation_required"):
                    print(f"[RISK] HIGH-RISK auto-topic blocked at pipeline level: '{topic_text}'")
                    send_message(
                        f"⚠️ HIGH-RISK topic blocked (auto mode):\n{topic_text}\n\n"
                        f"Risk signals: {_risk_auto.get('matched_signals', [])}\n\n"
                        f"Send a topic manually tomorrow to override with editorial-assist mode."
                    )
                    return
                topic["risk_info"] = _risk_auto
            except Exception as _risk_e:
                print(f"[RISK] Auto classification failed (non-fatal): {_risk_e}")
                topic.setdefault("risk_info", {})

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

        # ══════════════════════════════════════════════════════════════════════
        # STEP 2: Language-isolated script generation
        #
        # English and Arabic pipelines run INDEPENDENTLY.
        # Failure of one language does NOT abort the other.
        #   • English: write_script(topic, "english") — standard path
        #   • Arabic:  write_arabic_script(topic, research) — native, no EN dependency
        # ══════════════════════════════════════════════════════════════════════
        print("\n[2/5] Writing scripts (EN + AR independently)...")

        # ── 2A: English pipeline ───────────────────────────────────────────────
        en_long = None
        try:
            en_long = write_script(topic, language="english")
            _log("Scripts", f"EN done: '{en_long.get('title','?')}' | "
                 f"{len(en_long.get('script','').split())}w", "OK")
        except Exception as _en_e:
            _log("Scripts", f"EN script failed: {_en_e}", "ERROR")
            send_message(f"[Pipeline] English script failed: {_en_e}")
            # Create minimal fallback so Arabic can still proceed
            en_long = {
                "topic":           topic.get("topic", ""),
                "title":           topic.get("topic", ""),
                "script":          "",
                "language":        "english",
                "series_name":     topic.get("series_name", ""),
                "series_type":     topic.get("series_type", ""),
                "on_screen_texts": [],
                "caption":         "",
                "hashtags":        "",
                "chapters":        "",
                "hook":            "",
                "keywords":        topic.get("keywords", []),
                "short_script_en": "",
                "script_failed":   True,
            }

    _stage("Scripts EN done")

    # ── 2B: Arabic pipeline (independent — no en_long dependency) ─────────────
    ar_long = None
    _research = topic.get("research", {}) if topic else {}
    try:
        ar_long = write_arabic_script(topic, _research)
        # Regenerate chapters using actual Arabic word count
        _ar_wc = len(ar_long.get("script", "").split())
        if _ar_wc > 0:
            ar_long["chapters"] = generate_chapters(
                _ar_wc, language="arabic",
                angle_title=en_long.get("angle_title", "") if en_long else "",
            )
        # Forward angle fields from English if available (best-effort)
        if en_long:
            ar_long.setdefault("angle_title", en_long.get("angle_title", ""))
            ar_long.setdefault("angle_hook",  en_long.get("angle_hook",  ""))
        _log("Scripts", f"AR done: '{ar_long.get('title','?')}' | "
             f"{_ar_wc}w | path={ar_long.get('arabic_path','?')}", "OK")
    except Exception as _ar_e:
        _log("Scripts", f"AR script failed: {_ar_e}", "ERROR")
        send_message(f"[Pipeline] Arabic script failed (non-fatal — EN will still run): {_ar_e}")
        ar_long = None   # Arabic render will be skipped below

    # ── 2C: Short scripts (independent per language) ───────────────────────────
    _anim_mode_dc = os.getenv("PIPELINE_MODE", "").lower() == "animation"
    if not _anim_mode_dc:
        # English short — from English long script only
        if en_long and not en_long.get("script_failed"):
            try:
                _en_short_data = write_short_script(en_long)
                en_long["short_script_en"] = _en_short_data.get("short_script_en", "")
                _log("Scripts", "EN short done", "OK")
            except Exception as _es_e:
                _log("Scripts", f"EN short failed (non-fatal): {_es_e}", "WARN")
                en_long.setdefault("short_script_en", "")
        else:
            en_long.setdefault("short_script_en", "")

        # Arabic short — from Arabic long script INDEPENDENTLY
        if ar_long and not ar_long.get("script_too_short"):
            try:
                _ar_short_data = write_arabic_short(ar_long)
                ar_long["short_script_ar"] = _ar_short_data.get("short_script_ar", "")
                _log("Scripts", "AR short done (independent)", "OK")
            except Exception as _as_e:
                _log("Scripts", f"AR short failed (non-fatal): {_as_e}", "WARN")
                ar_long.setdefault("short_script_ar", "")
        elif ar_long:
            ar_long.setdefault("short_script_ar", "")
    else:
        _log("Scripts", "Animation mode — short script generation skipped", "INFO")
        en_long.setdefault("short_script_en", "")
        if ar_long:
            ar_long.setdefault("short_script_ar", "")

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

    # Send short scripts to Telegram (skipped in animation mode)
    if not _anim_mode_dc:
        _short_en_text = en_long.get("short_script_en", "")
        _short_ar_text = ar_long.get("short_script_ar", "")
        if _short_en_text:
            try:
                send_english_script_preview({**en_long, "script": _short_en_text},
                                            label="English SHORT script (45-90s)")
            except Exception as e:
                print(f"  [WARN] EN short script preview failed: {e}")
        if _short_ar_text:
            try:
                send_arabic_script_preview({**ar_long, "script": _short_ar_text},
                                           label="Arabic SHORT script (45-90s)")
            except Exception as e:
                print(f"  [WARN] AR short script preview failed: {e}")

    _log("Telegram", "Scripts sent to Telegram — waiting for approval", "OK")
    _stage("Scripts sent to Telegram")

    # ── Approval gate 1: Scripts ─────────────────────────────────────────────
    while True:
        _approval_1 = wait_for_approval(
            stage_name=f"Scripts Ready — {(en_long.get('title') or '')[:60]}\nReview the scripts above.",
            available_commands=["approve", "rewrite", "cancel"],
            mode="PIPELINE",
        )
        if _approval_1 == "cancel":
            send_message("[Pipeline] Cancelled at scripts gate.")
            return
        elif _approval_1 == "approve":
            break
        elif _approval_1 == "rewrite":
            if topic is None:
                send_message("[Pipeline] Rewrite unavailable for content-file ingested scripts.")
                continue
            _log("Scripts", "Rewrite requested — regenerating", "WARN")
            send_message("[Pipeline] Rewriting scripts...")
            try:
                en_long = write_script(topic, language="english")
                ar_long = translate_script(en_long, research=topic.get("research", {}))
                _short_rw = write_short_script(en_long)
                en_long["short_script_en"] = _short_rw.get("short_script_en", "")
                ar_long["short_script_ar"] = _short_rw.get("short_script_ar", "")
                send_arabic_script_preview(ar_long, label="Arabic LONG script (rewrite)")
                send_english_script_preview(en_long, label="English LONG script (rewrite)")
            except Exception as _re:
                send_message(f"[Pipeline] Rewrite failed: {_re}")

    # ── Load saved Part 2 images if this is a Part 2 run ──────
    _part_num_final = en_long.get("part_number")
    if _part_num_final == 2:
        _p2_paths = load_part2_images(en_long.get("topic", ""))
        if _p2_paths:
            _p2_dicts = [{"path": p, "tags": ["portrait", "real"]} for p in _p2_paths]
            user_images = _p2_dicts + list(user_images)
            print(f"[Pipeline] Added {len(_p2_paths)} saved Part 2 images")
            send_message(f"[Pipeline] Loaded {len(_p2_paths)} saved images for Part 2")

    # ── Load GitHub content library for this topic (retry up to 5x) ──────────
    _topic_for_media = en_long.get("topic", "")
    _gh_images: list = []
    _gh_videos: list = []
    _gh_music_long  = None
    _gh_music_short = None
    for _media_attempt in range(5):
        _gh_images, _gh_videos, _gh_music_long, _gh_music_short = load_all_content(_topic_for_media)
        if _gh_images or _gh_videos:
            break
        if _media_attempt < 4:
            _log("Media", f"No media loaded (attempt {_media_attempt + 1}/5) — retrying in 1s", "WARN")
            time.sleep(1)
    # If folder exists but no media loaded, switch to AI/web fallback mode
    _content_folder = find_content_folder(_topic_for_media)

    if _content_folder and os.path.exists(_content_folder) and not (_gh_images or _gh_videos):
        _log(
            "Media",
            f"No local media found in '{_content_folder}' "
            f"— switching to AI/web fallback mode",
            "WARN"
        )

        _gh_images = []
        _gh_videos = []
        
    if _gh_images or _gh_videos:
        print(f"[MEDIA] Loaded {len(_gh_images)} images, {len(_gh_videos)} videos (user mode)")

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

    # Snapshot user content for short video generation — isolate from any
    # side-effects of long video assembly and re-load from GitHub content folder.
    _short_user_images = list(user_images)
    _short_user_videos = list(user_videos)

    # Skip regeneration if today's manifest already has valid files for this topic
    en_long_id, ar_long_id = "", ""
    _topic_for_dedup = en_long.get("topic", "") if en_long else (ar_long.get("topic", "") if ar_long else "")
    _ex_en, _ex_ar = _load_existing_outputs(today, _topic_for_dedup)
    if _ex_en and _ex_ar:
        en_long_path, ar_long_path = _ex_en, _ex_ar
        stats["skipped"] += 2
        _log("VideoGen", "Reusing existing video files — skipping generation", "OK")
    else:
        _slug = _topic_slug(_topic_for_dedup)

        # ── OUTPUT 1 — Arabic long-form (isolated — EN failure does not skip this) ──
        ar_long_path = ""
        if ar_long:
            _ar_wc_pre   = len(ar_long.get("script", "").split())
            _dc_mode     = os.getenv("PIPELINE_MODE", "fast").lower()
            _AR_WORD_MIN = {"fast": 4500, "animation": 5000, "full": 5000}.get(_dc_mode, 4500)
            if ar_long.get("script_too_short") or _ar_wc_pre < _AR_WORD_MIN:
                _block_msg = (
                    f"[AR BLOCKED] Script below hard minimum: {_ar_wc_pre}w < {_AR_WORD_MIN}w "
                    f"— blocking Arabic render to prevent invalid upload"
                )
                _log("VideoGen", _block_msg, "ERROR")
                send_message(_block_msg)
            else:
                _log("VideoGen", f"[AR AUDIO] Script: {_ar_wc_pre}w | est. ~{_ar_wc_pre/175:.1f}min")
                ar_long_id   = f"{today}_{_slug}_arabic_long"
                ar_long_path = _make_video(ar_long, ar_long_id, stats, user_images=user_images, user_videos=user_videos)
        else:
            _log("VideoGen", "[AR SKIPPED] Arabic script generation failed — no AR video", "WARN")

        # ── OUTPUT 2 — English long-form (isolated — AR failure does not skip this) ──
        en_long_path = ""
        if en_long and not en_long.get("script_failed"):
            en_long_id   = f"{today}_{_slug}_english_long"
            en_long_path = _make_video(en_long, en_long_id, stats, user_images=user_images, user_videos=user_videos)
        else:
            _log("VideoGen", "[EN SKIPPED] English script generation failed — no EN video", "WARN")

    # ── Arabic runtime validation — mode-specific tiered system ─────────────
    # FULL:  <30m=FAIL  30-44m=UNDER TARGET→expand  45-60m=IDEAL  60-90m=ACCEPTABLE  >90m=TOO LONG
    # ANIM:  <30m=FAIL  30-35m=UNDER TARGET→expand  35-60m=IDEAL  >60m=ACCEPTABLE
    # FAST:  <30m=FAIL  30-40m=IDEAL                >40m=TOO LONG
    _dc_mode       = os.getenv("PIPELINE_MODE", "fast").lower()
    _AR_IDEAL_SECS = {"fast": 1800, "animation": 2100, "full": 2700}.get(_dc_mode, 1800)
    _AR_MAX_SECS   = {"fast": 2400, "animation": 3600, "full": 5400}.get(_dc_mode, 2400)
    _AR_TGT_MIN    = {"fast": 35.0, "animation": 47.5, "full": 55.0}.get(_dc_mode, 35.0)
    _AR_TARGET_STR = {"fast": "30-40m", "animation": "35-60m", "full": "45-90m"}.get(_dc_mode, "30-40m")
    _AR_HARD_FAIL  = 1800  # 30 min — universal absolute floor
    _ar_rebuild    = 0
    _ar_max_rb     = 4
    _topic_text_ar = topic.get("topic", "") if topic else (ar_long.get("topic", "") if ar_long else "")
    while ar_long and ar_long_path and os.path.exists(ar_long_path):
        _ar_secs = _video_secs(ar_long_path)
        _ar_mins = _ar_secs / 60
        if _ar_secs < _AR_HARD_FAIL:
            _ar_status = "FAIL"
        elif _ar_secs < _AR_IDEAL_SECS:
            _ar_status = "UNDER TARGET"
        elif _ar_secs <= _AR_MAX_SECS:
            _ar_status = "IDEAL" if _ar_secs <= 3600 else "ACCEPTABLE LONGFORM"
        else:
            _ar_status = "TOO LONG"
        _log("VideoGen", f"[AR RUNTIME] Target: {_AR_TARGET_STR} | Rendered: {_ar_mins:.1f}m | Status: {_ar_status}")
        if _ar_status in ("IDEAL", "ACCEPTABLE LONGFORM"):
            _log("VideoGen", f"[AR PASSED] {_ar_mins:.1f}min — {_ar_status}", "OK")
            break
        if _ar_status == "TOO LONG":
            _log("VideoGen", f"[AR TOO LONG] {_ar_mins:.1f}min > {_AR_MAX_SECS//60}min — exporting as-is", "WARN")
            send_message(f"[Pipeline] AR {_ar_mins:.1f}min exceeds {_AR_MAX_SECS//60}min — exporting as-is")
            break
        _ar_rebuild += 1
        if _ar_rebuild > _ar_max_rb:
            _log("VideoGen", f"[AR EXPANSION] Limit reached — continuing with {_ar_mins:.1f}min", "WARN")
            send_message(f"[Pipeline] AR {_ar_mins:.1f}min after {_ar_max_rb} expansions — proceeding")
            break
        send_message(
            f"[Pipeline] AR {_ar_status}: {_ar_mins:.1f}min | target {_AR_IDEAL_SECS//60}min — "
            f"fallback expansion ({_ar_rebuild}/{_ar_max_rb})..."
        )
        from agent.script_agent import expand_arabic_runtime as _ear
        ar_long["script"] = _ear(ar_long["script"], target_min=_AR_TGT_MIN, topic=_topic_text_ar)
        _slug_rb     = _topic_slug(_topic_for_dedup)
        ar_long_id   = f"{today}_{_slug_rb}_arabic_long_rb{_ar_rebuild}"
        ar_long_path = _make_video(ar_long, ar_long_id, stats, user_images=user_images, user_videos=user_videos)

    # Output 3: Arabic short  ── script path or cut fallback (Arabic first)
    ar_chapter_shorts: list[dict] = []
    _ar_short_script = ar_long.get("short_script_ar", "") if ar_long else ""
    _ar_short_via_script = False
    _ar_slug = _topic_slug(_topic_for_dedup)
    if SHORT_MODE == "script" and _ar_short_script and ar_long:
        print("[Pipeline] Generating Arabic short from short script (TTS → video)...")
        _ar_short_data = {**ar_long, "script": _ar_short_script}
        _ar_short_id   = f"{today}_{_ar_slug}_arabic_short"
        _ar_short_path = _make_video(_ar_short_data, _ar_short_id, stats,
                                     user_images=_short_user_images, user_videos=_short_user_videos)
        _ar_short_via_script = bool(_ar_short_path)
        # Auto-expansion: up to 2 attempts if rendered < 60s
        if _ar_short_via_script:
            for _ar_s_rb in range(1, 3):
                _ar_s_secs = _video_secs(_ar_short_path)
                print(f"[SHORT RUNTIME] AR short: {_ar_s_secs:.1f}s")
                if _ar_s_secs >= 60:
                    _log("Shorts", f"[SHORT PASSED] AR short: {_ar_s_secs:.1f}s", "OK")
                    break
                _log("Shorts", f"[SHORT EXPANSION] AR short {_ar_s_secs:.1f}s < 60s — expanding (attempt {_ar_s_rb}/2)", "WARN")
                from agent.script_agent import expand_short_script as _ess
                _ar_short_script = _ess(_ar_short_script, "arabic", ar_long.get("topic", ""), 290)
                ar_long["short_script_ar"] = _ar_short_script
                _new_path = _make_video(
                    {**ar_long, "script": _ar_short_script},
                    f"{today}_{_ar_slug}_arabic_short_exp{_ar_s_rb}",
                    stats, user_images=_short_user_images, user_videos=_short_user_videos,
                )
                _ar_short_path = _new_path or _ar_short_path
            else:
                _ar_s_secs = _video_secs(_ar_short_path)
                if _ar_s_secs < 60:
                    _log("Shorts", f"[SHORT BLOCKED] AR short {_ar_s_secs:.1f}s < 60s after 2 expansions — will not send", "ERROR")
                    _ar_short_path = ""
        if _ar_short_path:
            ar_chapter_shorts = [{
                "path":        _ar_short_path,
                "title":       ar_long.get("title", "") if ar_long else "",
                "label":       "Best Short — TikTok + Instagram + YouTube Shorts",
                "chapter_idx": 1,
            }]
    else:
        _reason = "SHORT_MODE=cut" if _ar_short_script else ("AR script failed" if not ar_long else "short_script_ar missing")
        print(f"[Pipeline] Cutting best Arabic short from long video ({_reason})...")
        if ar_long and ar_long_path and os.path.exists(ar_long_path):
            _raw_cuts = cut_best_short(ar_long_path, ar_long)
            for _cut in _raw_cuts:
                _cut_secs = _video_secs(_cut.get("path", ""))
                print(f"[SHORT RUNTIME] AR short (cut): {_cut_secs:.1f}s")
                if _cut_secs >= 60:
                    ar_chapter_shorts.append(_cut)
                else:
                    _log("Shorts", f"[SHORT BLOCKED] AR cut short {_cut_secs:.1f}s < 60s — skipped", "ERROR")

    # Output 4: English short  ── script path or cut fallback
    en_chapter_shorts: list[dict] = []
    _en_short_script = en_long.get("short_script_en", "")
    _en_short_via_script = False
    if SHORT_MODE == "script" and _en_short_script:
        print("[Pipeline] Generating English short from short script (TTS → video)...")
        _en_short_data = {**en_long, "script": _en_short_script}
        _slug          = _topic_slug(en_long.get("topic", ""))
        _en_short_id   = f"{today}_{_slug}_english_short"
        _en_short_path = _make_video(_en_short_data, _en_short_id, stats,
                                     user_images=_short_user_images, user_videos=_short_user_videos)
        _en_short_via_script = bool(_en_short_path)
        # Auto-expansion: up to 2 attempts if rendered < 60s
        if _en_short_via_script:
            for _en_s_rb in range(1, 3):
                _en_s_secs = _video_secs(_en_short_path)
                print(f"[SHORT RUNTIME] EN short: {_en_s_secs:.1f}s")
                if _en_s_secs >= 60:
                    _log("Shorts", f"[SHORT PASSED] EN short: {_en_s_secs:.1f}s", "OK")
                    break
                _log("Shorts", f"[SHORT EXPANSION] EN short {_en_s_secs:.1f}s < 60s — expanding (attempt {_en_s_rb}/2)", "WARN")
                from agent.script_agent import expand_short_script as _ess
                _en_short_script = _ess(_en_short_script, "english", en_long.get("topic", ""), 200)
                en_long["short_script_en"] = _en_short_script
                _new_path = _make_video(
                    {**en_long, "script": _en_short_script},
                    f"{today}_{_slug}_english_short_exp{_en_s_rb}",
                    stats, user_images=_short_user_images, user_videos=_short_user_videos,
                )
                _en_short_path = _new_path or _en_short_path
            else:
                _en_s_secs = _video_secs(_en_short_path)
                if _en_s_secs < 60:
                    _log("Shorts", f"[SHORT BLOCKED] EN short {_en_s_secs:.1f}s < 60s after 2 expansions — will not send", "ERROR")
                    _en_short_path = ""
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
            _raw_cuts = cut_best_short(en_long_path, en_long)
            for _cut in _raw_cuts:
                _cut_secs = _video_secs(_cut.get("path", ""))
                print(f"[SHORT RUNTIME] EN short (cut): {_cut_secs:.1f}s")
                if _cut_secs >= 60:
                    en_chapter_shorts.append(_cut)
                else:
                    _log("Shorts", f"[SHORT BLOCKED] EN cut short {_cut_secs:.1f}s < 60s — skipped", "ERROR")

    _stage("Videos + shorts done")

    # Clear user images + videos AFTER all 4 videos are assembled
    import shutil as _shutil
    for _clear_dir in ("output/user_images", "output/user_videos"):
        try:
            if os.path.exists(_clear_dir):
                _shutil.rmtree(_clear_dir)
            os.makedirs(_clear_dir, exist_ok=True)
        except Exception as _ce:
            _log("Cleanup", f"Could not reset {_clear_dir}: {_ce}", "WARN")
    _log("Cleanup", "User media dirs reset for next run", "OK")

    # ── Approval gate 2: Render complete ─────────────────────────────────────
    while True:
        _approval_2 = wait_for_approval(
            stage_name="Render Complete — Ready to Upload",
            available_commands=["approve", "publish", "rerender", "cancel"],
            mode="PIPELINE",
        )
        if _approval_2 in ("approve", "publish"):
            break
        elif _approval_2 == "cancel":
            send_message("[Pipeline] Cancelled at render gate.")
            return
        elif _approval_2 == "rerender":
            _log("VideoGen", "Re-render requested — regenerating all videos", "WARN")
            send_message("[Pipeline] Re-rendering all videos...")
            # Clear stale short paths before rerender to prevent duplicate outputs
            en_chapter_shorts = []
            ar_chapter_shorts = []
            _slug        = _topic_slug(_topic_for_dedup)
            en_long_id   = f"{today}_{_slug}_english_long"
            en_long_path = _make_video(en_long, en_long_id, stats, user_images=user_images, user_videos=user_videos)
            if ar_long:
                ar_long_id   = f"{today}_{_slug}_arabic_long"
                ar_long_path = _make_video(ar_long, ar_long_id, stats, user_images=user_images, user_videos=user_videos)
            if en_long_path and os.path.exists(en_long_path):
                _en_ss = en_long.get("short_script_en", "")
                if _en_ss:
                    _p = _make_video({**en_long, "script": _en_ss},
                                     f"{today}_{_slug}_english_short",
                                     stats, user_images=user_images, user_videos=user_videos)
                    if _p and _video_secs(_p) >= 60:
                        en_chapter_shorts = [{"path": _p, "title": en_long.get("title", ""), "label": "Best Short", "chapter_idx": 1}]
                    else:
                        en_chapter_shorts = cut_best_short(en_long_path, en_long)
                else:
                    en_chapter_shorts = cut_best_short(en_long_path, en_long)
                en_chapter_shorts = [c for c in en_chapter_shorts if _video_secs(c.get("path", "")) >= 60]
            if ar_long and ar_long_path and os.path.exists(ar_long_path):
                _ar_ss = ar_long.get("short_script_ar", "")
                if _ar_ss:
                    _p = _make_video({**ar_long, "script": _ar_ss},
                                     f"{today}_{_slug}_arabic_short",
                                     stats, user_images=user_images, user_videos=user_videos)
                    if _p and _video_secs(_p) >= 60:
                        ar_chapter_shorts = [{"path": _p, "title": ar_long.get("title", ""), "label": "Best Short", "chapter_idx": 1}]
                    else:
                        ar_chapter_shorts = cut_best_short(ar_long_path, ar_long)
                else:
                    ar_chapter_shorts = cut_best_short(ar_long_path, ar_long)
                ar_chapter_shorts = [c for c in ar_chapter_shorts if _video_secs(c.get("path", "")) >= 60]

    # ── STEP 5: FINALIZE → VALIDATE → PUBLISH ────────────────────────────────
    _log("Finalize", "Validating all outputs before publish")
    _stage("Finalize + validate")

    def _ffprobe_res(path: str) -> str:
        """Return WxH resolution string from ffprobe, e.g. '1920x1080'."""
        try:
            import subprocess as _sp
            import shutil as _sh
            _ffp = _sh.which("ffprobe") or _sh.which("ffmpeg")
            if not _ffp:
                return "?"
            _r = _sp.run(
                [_ffp, "-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=s=x:p=0", path],
                capture_output=True, text=True, timeout=15,
            )
            res = _r.stdout.strip()
            return res if res else "?"
        except Exception:
            return "?"

    def _output_row(label: str, path: str, is_short: bool = False) -> bool:
        """Log one output row; return True if it passes minimum thresholds."""
        if not path or not os.path.exists(path):
            _log("Validate", f"MISSING  {label}: path not found ({path})", "ERROR")
            return False
        mb   = os.path.getsize(path) // 1024 // 1024
        secs = _video_secs(path)
        mins = secs / 60
        res  = _ffprobe_res(path)
        min_secs = 55.0 if is_short else 300.0
        min_mb   = 1    if is_short else 5
        ok = secs >= min_secs and mb >= min_mb
        status = "OK " if ok else "FAIL"
        _log("Validate",
             f"{status}   {label}: {mins:.1f}min ({secs:.0f}s), {mb}MB, {res} — {path}",
             "OK" if ok else "ERROR")
        return ok

    _en_long_ok  = _output_row("EN long ", en_long_path,  is_short=False)
    _ar_long_ok  = _output_row("AR long ", ar_long_path,  is_short=False)
    _en_short_ok = _output_row(
        "EN short", en_chapter_shorts[0]["path"] if en_chapter_shorts else "", is_short=True
    )
    _ar_short_ok = _output_row(
        "AR short", ar_chapter_shorts[0]["path"] if ar_chapter_shorts else "", is_short=True
    )

    _valid_count = sum([_en_long_ok, _ar_long_ok, _en_short_ok, _ar_short_ok])
    _log("Validate", f"Output validation: {_valid_count}/4 outputs valid",
         "OK" if _en_long_ok else "ERROR")

    # Block upload if the EN long is missing — it's the primary deliverable
    if not _en_long_ok and not en_long_path:
        _crit = "[CRITICAL] EN long video missing — cannot upload. Check logs above."
        _log("Validate", _crit, "ERROR")
        send_message(f"[Pipeline] {_crit}")

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
        _en_exists = os.path.exists(en_long_path)
        _en_mb = os.path.getsize(en_long_path) // 1024 // 1024 if _en_exists else 0
        _log("Publish", f"EN video on disk: {_en_exists} | path: {en_long_path} | size: {_en_mb}MB",
             "OK" if _en_exists else "ERROR")
        try:
            _log("Publish", "Uploading English long to YouTube...")
            yt_en_url = _with_retry(upload_to_youtube, en_long_path, en_long,
                                    token_file=YOUTUBE_TOKEN_FILE_EN,
                                    retries=3, delay=30, label="YT EN upload")
            # upload_to_youtube catches its own exceptions and returns "" on failure,
            # so we must check the return value — a raised exception here is unlikely.
            if yt_en_url:
                send_message(
                    f"✅ English Video Published on YouTube!\n\n"
                    f"🎬 {en_long.get('title', '')}\n"
                    f"🔗 {yt_en_url}\n\n"
                    f"Duration: {get_duration(en_long_path)}"
                )
                _log("Publish", f"English YouTube: {yt_en_url}", "OK")
            else:
                _log("Publish",
                     "upload_to_youtube returned empty string — scroll up for [Publish] ERROR + traceback",
                     "ERROR")
                _fail_msg = "❌ English YouTube upload failed (upload_to_youtube returned empty URL)"
                if _artifact_url:
                    _fail_msg += f"\n\nDownload video:\n{_artifact_url}"
                send_message(_fail_msg)
                stats["errors"] += 1
        except Exception as e:
            _log("Publish", f"English YouTube upload raised exception: {e}", "ERROR")
            _log("Publish", traceback.format_exc(), "ERROR")
            _fail_msg = f"❌ English YouTube upload failed: {e}"
            if _artifact_url:
                _fail_msg += f"\n\nDownload video from GitHub artifact:\n{_artifact_url}"
            send_message(_fail_msg)
            stats["errors"] += 1

    yt_ar_url = None
    if ar_long_path:
        _ar_exists = os.path.exists(ar_long_path)
        _ar_mb = os.path.getsize(ar_long_path) // 1024 // 1024 if _ar_exists else 0
        _log("Publish", f"AR video on disk: {_ar_exists} | path: {ar_long_path} | size: {_ar_mb}MB",
             "OK" if _ar_exists else "ERROR")
        try:
            _log("Publish", "Uploading Arabic long to YouTube...")
            yt_ar_url = _with_retry(upload_to_youtube, ar_long_path, ar_long,
                                    token_file=YOUTUBE_TOKEN_FILE_AR,
                                    retries=3, delay=30, label="YT AR upload")
            if yt_ar_url:
                send_message(
                    f"✅ تم نشر الفيديو العربي على يوتيوب!\n\n"
                    f"🎬 {ar_long.get('title', '')}\n"
                    f"🔗 {yt_ar_url}\n\n"
                    f"المدة: {get_duration(ar_long_path)}"
                )
                _log("Publish", f"Arabic YouTube: {yt_ar_url}", "OK")
            else:
                _log("Publish",
                     "upload_to_youtube returned empty string — scroll up for [Publish] ERROR + traceback",
                     "ERROR")
                _fail_msg = "❌ Arabic YouTube upload failed (upload_to_youtube returned empty URL)"
                if _artifact_url:
                    _fail_msg += f"\n\nDownload video:\n{_artifact_url}"
                send_message(_fail_msg)
                stats["errors"] += 1
        except Exception as e:
            _log("Publish", f"Arabic YouTube upload raised exception: {e}", "ERROR")
            _log("Publish", traceback.format_exc(), "ERROR")
            _fail_msg = f"❌ Arabic YouTube upload failed: {e}"
            if _artifact_url:
                _fail_msg += f"\n\nDownload video from GitHub artifact:\n{_artifact_url}"
            send_message(_fail_msg)
            stats["errors"] += 1

    # Send best English short to Telegram (1 video, reliable)
    if en_chapter_shorts:
        short = en_chapter_shorts[0]
        _s_path = short.get("path", "")
        _s_exists = bool(_s_path) and os.path.exists(_s_path)
        _s_mb = os.path.getsize(_s_path) / 1024 / 1024 if _s_exists else 0
        _log("Telegram", f"EN short: path={_s_path} | exists={_s_exists} | size={_s_mb:.1f}MB")
        if not _s_exists:
            _log("Telegram", f"EN short video file missing on disk — cannot send", "ERROR")
        else:
            try:
                caption = (
                    f"MANUAL POST NEEDED\n\n"
                    f"{short['title']}\n"
                    f"Post to: {short['label']}\n\n"
                    f"Topic: {en_long.get('title', '')}\n"
                    f"{en_long.get('hashtags', '')}"
                )
                _with_retry(send_video_to_telegram, _s_path, caption,
                            "EN Best Short",
                            retries=3, delay=10, label="TG EN Best Short")
                _log("Telegram", "EN best short sent to Telegram", "OK")
            except Exception as e:
                _log("Telegram", f"EN best short send failed: {e}", "WARN")
                _log("Telegram", traceback.format_exc(), "WARN")

    # Send best Arabic short to Telegram (1 video, reliable)
    if ar_chapter_shorts:
        short = ar_chapter_shorts[0]
        _s_path = short.get("path", "")
        _s_exists = bool(_s_path) and os.path.exists(_s_path)
        _s_mb = os.path.getsize(_s_path) / 1024 / 1024 if _s_exists else 0
        _log("Telegram", f"AR short: path={_s_path} | exists={_s_exists} | size={_s_mb:.1f}MB")
        if not _s_exists:
            _log("Telegram", f"AR short video file missing on disk — cannot send", "ERROR")
        else:
            try:
                caption = (
                    f"MANUAL POST NEEDED\n\n"
                    f"{short['title']}\n"
                    f"Post to: {short['label']}\n\n"
                    f"Topic: {ar_long.get('title', '')}\n"
                    f"{ar_long.get('hashtags', '')}"
                )
                _with_retry(send_video_to_telegram, _s_path, caption,
                            "AR Best Short",
                            retries=3, delay=10, label="TG AR Best Short")
                _log("Telegram", "AR best short sent to Telegram", "OK")
            except Exception as e:
                _log("Telegram", f"AR best short send failed: {e}", "WARN")
                _log("Telegram", traceback.format_exc(), "WARN")

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
    print("[Render] Pipeline exiting cleanly")
    # Force-exit to prevent daemon threads / orphan ffmpeg processes from
    # keeping the GitHub Actions runner alive indefinitely after completion.
    import gc as _gc
    _gc.collect()
    sys.exit(0)


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


def _video_secs(path: str) -> float:
    """Return actual video duration in seconds (0 on error). ffprobe-first to avoid file locks."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            val = result.stdout.strip()
            if val and val != "N/A":
                return float(val)
    except Exception:
        pass
    try:
        try:
            from moviepy.editor import VideoFileClip
        except ImportError:
            from moviepy import VideoFileClip
        c = VideoFileClip(path)
        d = c.duration
        c.close()
        return d
    except Exception:
        return 0.0


def _make_video(script_data: dict, video_id: str, stats: dict, user_images: list | None = None, user_videos: list | None = None) -> str:
    """Create a video using ElevenLabs + Pollinations, update stats, return path."""
    try:
        _raw = create_video(script_data, video_id, user_images=user_images, user_videos=user_videos)
        path = _raw[0] if isinstance(_raw, tuple) else _raw  # guard against accidental tuple return
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
            "ar_long_title": ar_long.get("title", "") if ar_long else "",
        },
        "script_data": {
            "en_long": {k: en_long.get(k, "") for k in ("title", "description", "tags", "language", "niche")},
            "ar_long": {k: ar_long.get(k, "") for k in ("title", "description", "tags", "language", "niche")} if ar_long else {},
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
