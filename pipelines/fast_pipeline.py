# pipelines/fast_pipeline.py
#
# FAST PIPELINE — speed-optimised, CI-safe, independent of full_pipeline.py.
#
# What is cut vs FULL:
#   ✗  No 60-second Telegram topic-wait
#   ✗  No 3-minute photo-wait
#   ✗  No Part-2 image loading
#   ✗  No content-library retry loop (single attempt only)
#   ✗  No failed-upload recovery
#   ✗  No image enhancement pass
#   ✗  No Whisper subtitle burn
#   ✗  No quality post-processing (video_quality.py)
#
# What is kept:
#   ✓  Auto-research (DuckDuckGo, 1 topic)
#   ✓  English + Arabic script generation
#   ✓  ElevenLabs TTS
#   ✓  Fast clip selection (select_best_clips_fast, max 6 clips, no scoring)
#   ✓  Pollinations AI image generation
#   ✓  Standard video assembly (assemble_video_with_hook / assemble_short_video)
#   ✓  YouTube upload (EN + AR long)
#   ✓  Telegram short delivery (EN + AR)
#
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
from agent.script_agent   import write_script, translate_script, generate_chapters, write_short_script, clean_word_count
from agent.video_agent    import create_video, ensure_music_assets, cut_best_short, load_all_content
from agent.notify_agent   import (
    send_message, send_video_to_telegram, send_daily_report,
)
from agent.publish_agent  import upload_to_youtube
from pipelines.pipeline_config import SCRIPT_WORD_MIN, WORDS_PER_MINUTE


# ── Helpers ──────────────────────────────────────────────────────────────────

def _log(stage: str, msg: str, level: str = "INFO") -> None:
    ts  = datetime.datetime.now().strftime("%H:%M:%S")
    tag = {"WARN": "WARN", "ERROR": "ERR ", "OK": "OK  "}.get(level, "INFO")
    print(f"[{ts}][{tag}][{stage}] {msg}", flush=True)


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
        _log("VideoGen", f"{video_id}: {e}", "ERROR")
        send_message(f"[FAST] Video failed for {video_id}: {e}")
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
    t0    = time.time()
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    print(f"\n{'='*60}")
    print(f"  [FAST PIPELINE] Dark Crime Decoded — {today}")
    print(f"  PIPELINE_MODE = {os.getenv('PIPELINE_MODE','fast')}")
    print(f"{'='*60}\n")

    ensure_music_assets()

    # ── STEP 1: Auto-research one topic ──────────────────────────────────────
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

    _log("Research", f"Topic: '{topic_text}'", "OK")
    send_message(f"[FAST PIPELINE] Topic: {topic_text}\n\nStarting fast generation...")

    series = topic_niche.split("behind")[-1].strip() if "behind" in topic_niche else topic_text
    try:
        research = research_series(series, user_note=topic.get("user_note"))
        if research is None:
            research = {}
        research["real_person"] = topic_text
        topic["research"]       = research
    except Exception as e:
        _log("Research", f"research_series failed (non-fatal): {e}", "WARN")
        topic["research"] = {}

    # ── STEP 2: Scripts (EN + AR) ─────────────────────────────────────────────
    _log("Scripts", "Writing English script")
    try:
        en_long = write_script(topic, language="english")
    except Exception as e:
        send_message(f"[FAST] Script failed: {e}")
        _log("Scripts", str(e), "ERROR")
        return

    # Enforce minimum duration floor: reject scripts that would produce < 10 min video.
    _en_wc = clean_word_count(en_long.get("script", ""))
    _est_min = round(_en_wc / WORDS_PER_MINUTE, 1)
    if _en_wc < SCRIPT_WORD_MIN:
        _msg = (
            f"[FAST] Script too short: {_en_wc} words (~{_est_min} min) — "
            f"minimum is {SCRIPT_WORD_MIN} words (~{SCRIPT_WORD_MIN // WORDS_PER_MINUTE} min). Aborting."
        )
        _log("Scripts", _msg, "ERROR")
        send_message(_msg)
        return
    _log("Scripts", f"Length OK: {_en_wc} words (~{_est_min} min)", "OK")

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
    except Exception as e:
        _log("Scripts", f"Arabic translation failed (non-fatal): {e}", "WARN")
        ar_long = dict(en_long)
        ar_long["language"] = "arabic"

    try:
        _short_data = write_short_script(en_long)
        en_long["short_script_en"] = _short_data.get("short_script_en", "")
        ar_long["short_script_ar"] = _short_data.get("short_script_ar", "")
    except Exception as e:
        _log("Scripts", f"Short script failed (non-fatal): {e}", "WARN")

    # ── STEP 3: Content library (single attempt) ──────────────────────────────
    _topic_for_media             = en_long.get("topic", "")
    gh_images, gh_videos, _, _  = load_all_content(_topic_for_media)
    user_images: list[dict]      = [{"path": p, "tags": [], "caption": os.path.basename(p)} for p in gh_images]
    user_videos: list[dict]      = list(gh_videos)
    if gh_images or gh_videos:
        _log("Media", f"{len(gh_images)} images + {len(gh_videos)} videos loaded", "OK")

    # ── STEP 4: Generate 4 videos ─────────────────────────────────────────────
    _log("VideoGen", "Generating EN + AR long + short videos")

    en_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_english_long"
    ar_long_id   = f"{today}_{uuid.uuid4().hex[:8]}_arabic_long"
    en_long_path = _make_video(en_long, en_long_id, stats, user_images=user_images, user_videos=user_videos)
    ar_long_path = _make_video(ar_long, ar_long_id, stats, user_images=user_images, user_videos=user_videos)

    # Short clips: script-based if available, otherwise cut from long video
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

    send_daily_report(stats)
    _result = "SUCCESS" if stats["errors"] == 0 else f"PARTIAL ({stats['errors']} error(s))"
    print(f"\n{'='*60}")
    print(f"  [FAST] {_result} — {today}  ({elapsed:.0f} min)")
    print(f"  Generated: {stats['generated']} | Errors: {stats['errors']}")
    print(f"{'='*60}\n")
