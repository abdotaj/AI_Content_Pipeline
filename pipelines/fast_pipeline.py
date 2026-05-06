# pipelines/fast_pipeline.py
#
# FAST PIPELINE — optimized workflow, production-quality output.
#
# PHILOSOPHY: FAST means an efficient execution path — NOT lower quality.
# Both FAST and FULL produce upload-ready professional Arabic documentary videos.
# The difference is processing depth, not storytelling or script standards.
#
# What FAST skips (redundant overhead, not quality):
#   ✗  60-second Telegram topic-wait (auto-selects instead)
#   ✗  3-minute photo-wait (images ready immediately)
#   ✗  Part-2 image loading (single pass)
#   ✗  Content-library retry loop (single attempt only)
#   ✗  Failed-upload recovery (notify and move on)
#   ✗  Image enhancement pass (Pollinations quality is sufficient)
#   ✗  Whisper subtitle burn (narration is clear without captions)
#   ✗  Quality post-processing (video_quality.py)
#   ✗  Exhaustive clip scoring (uses lighter select_best_clips_fast)
#
# What FAST keeps (everything that matters for quality):
#   ✓  Deep research (DuckDuckGo + research_series)
#   ✓  Full-length English script (1,800–2,500 words = 11–16 min)
#   ✓  Strong Arabic translation (Google Translate + proper chapters)
#   ✓  ElevenLabs cloned voices (same quality as FULL)
#   ✓  Pollinations AI image generation (10 images per long video)
#   ✓  Library clip selection (select_best_clips_fast)
#   ✓  Standard cinematic assembly (hook + chapters + outro)
#   ✓  YouTube upload (EN + AR long videos)
#   ✓  Telegram short delivery (EN + AR)
#
# Both modes enforce a hard 10-minute minimum (1,560 words @ 156 WPM).
# PIPELINE_MODE=fast is guaranteed by run_fast.py before this module loads.

import os
import sys
import json
import uuid
import time
import datetime
import traceback
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config_darkcrimed
sys.modules.setdefault("config", config_darkcrimed)

from config_darkcrimed import (
    FINAL_DIR, CONTENT_DIR, YOUTUBE_TOKEN_FILE_EN, YOUTUBE_TOKEN_FILE_AR,
)

from agent.research_agent import research_topics, research_series, mark_covered, is_fictional
from agent.script_agent   import write_script, translate_script, generate_chapters, write_short_script, clean_word_count, expand_script_runtime
from agent.video_agent    import create_video, ensure_music_assets, cut_best_short, load_all_content
from agent.notify_agent   import (
    send_message, send_video_to_telegram, send_daily_report,
    send_english_script_preview, send_arabic_script_preview, send_document,
)
from agent.publish_agent  import upload_to_youtube
from pipelines.pipeline_config import SCRIPT_WORD_FLOOR, SCRIPT_WORD_MIN, WORDS_PER_MINUTE
from pipelines.telegram_control import TelegramController, CANCEL_FLAG


# ── Helpers ──────────────────────────────────────────────────────────────────

_ctrl: TelegramController | None = None


def _log(stage: str, msg: str, level: str = "INFO") -> None:
    ts   = datetime.datetime.now().strftime("%H:%M:%S")
    tag  = {"WARN": "WARN", "ERROR": "ERR ", "OK": "OK  "}.get(level, "INFO")
    line = f"[{ts}][{tag}][{stage}] {msg}"
    print(line, flush=True)
    if _ctrl is not None:
        _ctrl.add_log(line)


def _check_cancel(stage: str = "") -> None:
    """Exit cleanly if /cancel was received via Telegram."""
    if _ctrl is None or not _ctrl.is_cancelled():
        return
    note = f" during {stage}" if stage else ""
    send_message(f"[FAST] Pipeline cancelled safely{note}.")
    print(f"[FAST] Cancelled{note} — exiting.")
    try:
        from agent.video_agent import _kill_orphan_ffmpeg
        _kill_orphan_ffmpeg()
    except Exception:
        pass
    if _ctrl:
        _ctrl.stop()
    import gc as _gc
    _gc.collect()
    sys.exit(0)


def _make_video(script_data: dict, video_id: str, stats: dict,
                user_images: list | None = None,
                user_videos: list | None = None) -> str:
    try:
        raw  = create_video(script_data, video_id,
                            user_images=user_images, user_videos=user_videos)
        path = raw[0] if isinstance(raw, tuple) else raw
        if path and Path(path).exists():
            stats["generated"] += 1
            _log("VideoGen", f"Ready: {path}", "OK")
            return path
        raise RuntimeError("create_video returned no file")
    except Exception as e:
        traceback.print_exc()
        _log("VideoGen", f"{video_id}: {type(e).__name__}: {e}", "ERROR")
        send_message(f"[FAST] Video failed for {video_id}: {type(e).__name__}: {e}")
        stats["errors"] += 1
        return ""


def get_duration(video_path: str) -> str:
    try:
        from moviepy import VideoFileClip
        clip = VideoFileClip(video_path)
        d    = clip.duration
        clip.close()
        return f"{int(d // 60)}:{int(d % 60):02d}"
    except Exception:
        return "unknown"


# ── Main entry point ─────────────────────────────────────────────────────────

def run_pipeline() -> None:
    global _ctrl
    t0    = time.time()
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    # Initialise Telegram controller (listener starts after topic is known)
    _ctrl = TelegramController(mode="fast")

    print(f"\n{'='*60}")
    print(f"  [FAST PIPELINE] Dark Crime Decoded — {today}")
    print(f"  PIPELINE_MODE = {os.getenv('PIPELINE_MODE','fast')}")
    print(f"{'='*60}\n")

    ensure_music_assets()

    # ── STEP 1: Auto-research one topic ──────────────────────────────────────
    _ctrl.update_stage("Research", "auto-selecting topic")
    _log("Research", "Auto-selecting topic (no Telegram wait)")
    try:
        topics = research_topics(count=1)
        if not topics:
            raise RuntimeError("research_topics returned empty list")
        topic = topics[0]
    except Exception as e:
        send_message(f"[FAST] Research failed: {e}")
        _log("Research", str(e), "ERROR")
        return

    topic_text  = topic.get("topic", "")
    topic_niche = topic.get("niche", "")

    if is_fictional(topic_text, topic_niche):
        _log("Research", f"Fictional topic blocked: '{topic_text}'", "WARN")
        send_message(f"[FAST] Fictional topic blocked: '{topic_text}'")
        return

    # Topic confirmed — set in controller and start listener
    _ctrl.set_topic(topic_text)
    _ctrl.start()

    _log("Research", f"Topic: '{topic_text}'", "OK")
    send_message(f"[FAST PIPELINE] Topic: {topic_text}\n\nStarting fast generation...")

    series = topic_niche.split("behind")[-1].strip() if "behind" in topic_niche else topic_text
    _ctrl.update_stage("Research", f"deep research: {series}")
    try:
        research = research_series(series, user_note=topic.get("user_note"))
        if research is None:
            research = {}
        research["real_person"] = topic_text
        topic["research"]       = research
        _log("Research", "Deep research done", "OK")
    except Exception as e:
        _log("Research", f"research_series failed (non-fatal): {e}", "WARN")
        topic["research"] = {}

    # ── STEP 2: Scripts (EN + AR) ─────────────────────────────────────────────
    _ctrl.update_stage("Scripts", "writing English script")
    _log("Scripts", "Writing English script")
    try:
        en_long = write_script(topic, language="english")
    except Exception as e:
        traceback.print_exc()
        send_message(f"[FAST] Script failed: {type(e).__name__}: {e}")
        _log("Scripts", f"{type(e).__name__}: {e}", "ERROR")
        return

    # Enforce minimum duration.
    # Instead of aborting immediately on a short script, retry lightweight
    # section-level expansion up to 3 times to close small word-count gaps.
    # Only abort after retries are exhausted and the script is still too short.
    _en_wc   = clean_word_count(en_long.get("script", ""))
    _est_min = round(_en_wc / WORDS_PER_MINUTE, 1)
    _max_ret = 3
    _retry   = 0

    while _en_wc < SCRIPT_WORD_FLOOR and _retry < _max_ret:
        _missing = SCRIPT_WORD_FLOOR - _en_wc
        print(f"[FAST] Script short by {_missing} words ({_en_wc} words ~{_est_min} min)")
        _log("Scripts",
             f"Short by {_missing} words — expansion attempt {_retry + 1}/{_max_ret}",
             "WARN")
        print(f"[FAST] Expansion attempt {_retry + 1}...")
        _expanded = expand_script_runtime(
            en_long["script"], _missing, topic=en_long.get("topic", "")
        )
        _new_wc = clean_word_count(_expanded)
        if _new_wc > _en_wc:
            en_long["script"] = _expanded
            _en_wc   = _new_wc
            _est_min = round(_en_wc / WORDS_PER_MINUTE, 1)
            print(f"[FAST] New count: {_en_wc} words (~{_est_min} min)")
        else:
            print(f"[FAST] Expansion attempt {_retry + 1} produced no gain")
        _retry += 1

    if _en_wc < SCRIPT_WORD_FLOOR:
        _msg = (
            f"[FAST] Script still short after {_retry} expansion attempt(s): "
            f"{_en_wc} words (~{_est_min} min) — hard minimum is {SCRIPT_WORD_FLOOR}. Aborting."
        )
        _log("Scripts", _msg, "ERROR")
        send_message(_msg)
        return

    if _retry > 0:
        print(f"[FAST] Runtime target reached: {_en_wc} words (~{_est_min} min)")
        _log("Scripts",
             f"Runtime target reached after {_retry} expansion(s): {_en_wc} words (~{_est_min} min)",
             "OK")

    _log("Scripts", f"Script: {_en_wc} words — Est. runtime: ~{_est_min} min", "OK")
    if _en_wc < SCRIPT_WORD_MIN:
        _log("Scripts", f"Below preferred minimum ({SCRIPT_WORD_MIN}w) — proceeding; check pacing", "WARN")

    # ── Send English script preview to Telegram ───────────────────────────────
    _ctrl.set_latest_script(en_long)
    _ctrl.update_stage("Scripts", "sending script preview")
    try:
        send_english_script_preview(en_long, label=f"[FAST] SCRIPT READY — {en_long.get('title','')}")
        _log("Scripts", "English script sent to Telegram", "OK")
    except Exception as _e:
        _log("Scripts", f"Script preview (non-fatal): {_e}", "WARN")

    # Upload full script as .txt document
    _script_txt_path = f"output/fast_script_{today}.txt"
    try:
        os.makedirs("output", exist_ok=True)
        with open(_script_txt_path, "w", encoding="utf-8") as _sf:
            _sf.write(
                f"TITLE: {en_long.get('title','')}\n"
                f"TOPIC: {en_long.get('topic','')}\n"
                f"WORDS: {_en_wc}  EST: ~{_est_min} min\n"
                f"{'='*60}\n\n"
                f"{en_long.get('script','')}"
            )
        send_document(_script_txt_path, caption=f"[FAST] Full script — {en_long.get('title','')[:80]}")
        _log("Scripts", "Script .txt uploaded to Telegram", "OK")
    except Exception as _e:
        _log("Scripts", f"Script .txt upload (non-fatal): {_e}", "WARN")

    _check_cancel("after script generation")

    _ctrl.update_stage("Scripts", "translating to Arabic")
    try:
        ar_long = translate_script(en_long)
        ar_wc   = len(ar_long.get("script", "").split())
        if ar_wc > 0:
            ar_long["chapters"] = generate_chapters(
                ar_wc, language="arabic", angle_title=en_long.get("angle_title", "")
            )
        ar_long["angle_title"] = en_long.get("angle_title", "")
        ar_long["angle_hook"]  = en_long.get("angle_hook", "")
        _log("Scripts", "Arabic script done", "OK")
        # Send Arabic preview
        try:
            send_arabic_script_preview(ar_long)
        except Exception as _ae:
            _log("Scripts", f"Arabic preview (non-fatal): {_ae}", "WARN")
    except Exception as e:
        traceback.print_exc()
        _log("Scripts", f"Arabic translation failed (non-fatal): {type(e).__name__}: {e}", "WARN")
        ar_long = dict(en_long)
        ar_long["language"] = "arabic"

    _ctrl.update_stage("Scripts", "writing short scripts")
    try:
        _short_data = write_short_script(en_long)
        en_long["short_script_en"] = _short_data.get("short_script_en", "")
        ar_long["short_script_ar"] = _short_data.get("short_script_ar", "")
        _log("Scripts", "Short scripts done", "OK")
    except Exception as e:
        traceback.print_exc()
        _log("Scripts", f"Short script failed (non-fatal): {type(e).__name__}: {e}", "WARN")

    _check_cancel("after all scripts")

    # ── STEP 3: Content library (single attempt) ──────────────────────────────
    _ctrl.update_stage("Media", "loading content library")
    _topic_for_media             = en_long.get("topic", "")
    gh_images, gh_videos, _, _  = load_all_content(_topic_for_media)
    user_images: list[dict]      = [{"path": p, "tags": [], "caption": os.path.basename(p)} for p in gh_images]
    user_videos: list[dict]      = list(gh_videos)
    if gh_images or gh_videos:
        _log("Media", f"{len(gh_images)} images + {len(gh_videos)} videos loaded", "OK")

    # ── STEP 4: Generate 4 videos ─────────────────────────────────────────────
    _ctrl.update_stage("VideoGen", "rendering EN long video")
    _log("VideoGen", "Rendering EN long video")
    en_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_long"
    en_long_path = _make_video(en_long, en_long_id, stats, user_images=user_images, user_videos=user_videos)

    _check_cancel("after EN long render")

    _ctrl.update_stage("VideoGen", "rendering AR long video")
    _log("VideoGen", "Rendering AR long video")
    ar_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_long"
    ar_long_path = _make_video(ar_long, ar_long_id, stats, user_images=user_images, user_videos=user_videos)

    _check_cancel("after AR long render")

    # Short clips: script-based if available, otherwise cut from long video
    _ctrl.update_stage("VideoGen", "rendering EN short clip")
    en_short_path = ""
    ar_short_path = ""

    _en_short_script = en_long.get("short_script_en", "")
    if _en_short_script:
        _en_sid = f"{today}_{uuid.uuid4().hex[:8]}_english_short"
        en_short_path = _make_video({**en_long, "script": _en_short_script},
                                    _en_sid, stats,
                                    user_images=user_images, user_videos=user_videos)
    elif en_long_path and os.path.exists(en_long_path):
        shorts = cut_best_short(en_long_path, en_long)
        en_short_path = shorts[0]["path"] if shorts else ""

    _check_cancel("after long video renders")

    _ctrl.update_stage("VideoGen", "rendering short clips")
    _ar_short_script = ar_long.get("short_script_ar", "")
    if _ar_short_script:
        _ar_sid = f"{today}_{uuid.uuid4().hex[:8]}_arabic_short"
        ar_short_path = _make_video({**ar_long, "script": _ar_short_script},
                                    _ar_sid, stats,
                                    user_images=user_images, user_videos=user_videos)
    elif ar_long_path and os.path.exists(ar_long_path):
        shorts = cut_best_short(ar_long_path, ar_long)
        ar_short_path = shorts[0]["path"] if shorts else ""

    # ── STEP 5: Publish ───────────────────────────────────────────────────────
    _ctrl.update_stage("Publish", "uploading to YouTube")
    _run_id       = os.getenv("GITHUB_RUN_ID", "")
    _repo         = os.getenv("GITHUB_REPOSITORY", "abdotaj/AI_Content_Pipeline")
    _artifact_url = f"https://github.com/{_repo}/actions/runs/{_run_id}" if _run_id else ""

    yt_en_url = ""
    if en_long_path and os.path.exists(en_long_path):
        try:
            yt_en_url = upload_to_youtube(en_long_path, en_long, token_file=YOUTUBE_TOKEN_FILE_EN)
            if yt_en_url:
                send_message(f"[FAST] EN video live: {yt_en_url}")
                _log("Publish", f"EN: {yt_en_url}", "OK")
            else:
                _log("Publish", "EN upload returned empty URL", "ERROR")
                send_message(f"[FAST] EN upload failed{f' — {_artifact_url}' if _artifact_url else ''}")
                stats["errors"] += 1
        except Exception as e:
            _log("Publish", f"EN upload exception: {e}", "ERROR")
            send_message(f"[FAST] EN upload error: {e}")
            stats["errors"] += 1

    yt_ar_url = ""
    if ar_long_path and os.path.exists(ar_long_path):
        try:
            yt_ar_url = upload_to_youtube(ar_long_path, ar_long, token_file=YOUTUBE_TOKEN_FILE_AR)
            if yt_ar_url:
                send_message(f"[FAST] AR video live: {yt_ar_url}")
                _log("Publish", f"AR: {yt_ar_url}", "OK")
            else:
                _log("Publish", "AR upload returned empty URL", "ERROR")
                send_message(f"[FAST] AR upload failed{f' — {_artifact_url}' if _artifact_url else ''}")
                stats["errors"] += 1
        except Exception as e:
            _log("Publish", f"AR upload exception: {e}", "ERROR")
            send_message(f"[FAST] AR upload error: {e}")
            stats["errors"] += 1

    # Send shorts to Telegram
    for short_path, lang_label, script in [
        (en_short_path, "EN", en_long),
        (ar_short_path, "AR", ar_long),
    ]:
        if short_path and os.path.exists(short_path):
            try:
                caption = (
                    f"[FAST] MANUAL POST NEEDED\n\n"
                    f"{script.get('title','')}\n"
                    f"Post to: TikTok + Instagram + YouTube Shorts\n\n"
                    f"{script.get('hashtags','')}"
                )
                send_video_to_telegram(short_path, caption, f"{lang_label} Short")
                _log("Telegram", f"{lang_label} short sent", "OK")
            except Exception as e:
                _log("Telegram", f"{lang_label} short send failed: {e}", "WARN")

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (time.time() - t0) / 60
    _status_en = f"✅ {yt_en_url}" if yt_en_url else "❌ Failed"
    _status_ar = f"✅ {yt_ar_url}" if yt_ar_url else "❌ Failed"
    send_message(
        f"📊 [FAST PIPELINE] Done — {today}\n\n"
        f"⏱ Time: {elapsed:.0f} min\n"
        f"🎬 EN → {_status_en}\n"
        f"🎬 AR → {_status_ar}\n"
        f"📱 EN short: {'✅' if en_short_path else '❌'}\n"
        f"📱 AR short: {'✅' if ar_short_path else '❌'}"
    )

    series_name = en_long.get("series") or en_long.get("niche", "").split("behind")[-1].strip()
    if series_name:
        try:
            mark_covered(series_name, en_long_id)
        except Exception:
            pass

    _ctrl.update_stage("Done")
    _ctrl.stop()

    send_daily_report(stats)
    _result = "SUCCESS" if stats["errors"] == 0 else f"PARTIAL ({stats['errors']} error(s))"
    print(f"\n{'='*60}")
    print(f"  [FAST] {_result} — {today}  ({elapsed:.0f} min)")
    print(f"  Generated: {stats['generated']} | Errors: {stats['errors']}")
    print(f"{'='*60}\n")
