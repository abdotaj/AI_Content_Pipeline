# pipelines/animation_pipeline.py
#
# ANIMATION PIPELINE — AI-animated character-centric documentary mode.
#
# PHILOSOPHY: Motion-first, identity-consistent visual storytelling.
# Every frame is motion. No static slideshow. One visual style per video.
# Character identity is locked on the first real photo fetch and injected
# into every subsequent scene prompt — so the person looks the same across
# all 20+ clips.
#
# Visual generation tiers (per scene):
#   Tier 1: D-ID talking portrait (hook section — real photo + audio)
#   Tier 2: Runway Gen-3 Turbo  (image + motion prompt → clip)
#   Tier 3: Luma Dream Machine  (image + motion prompt → clip)
#   Tier 4: Kling AI            (image + motion prompt → clip)
#   Tier 5: Enhanced still      (MoviePy Ken Burns — always available)
#
# What ANIMATION keeps from FAST:
#   ✓  Deep research (DuckDuckGo + research_series)
#   ✓  Full-length English script (1,800–2,500 words = 11–16 min)
#   ✓  Strong Arabic script from research (research-first Arabic path)
#   ✓  ElevenLabs cloned voices (handled inside create_animation_video)
#   ✓  YouTube upload (EN + AR long videos)
#   ✓  Telegram short delivery (EN + AR, cut from long)
#
# What ANIMATION replaces:
#   ✗  Random Pollinations AI images → character-consistent motion clips
#   ✗  Static slideshow assembly → continuous-motion documentary
#   ✗  Generic image prompts → character-descriptor-injected scene prompts
#
# PIPELINE_MODE=animation is set by run_animation.py before this module loads.

import os
import sys
import uuid
import time
import datetime
import traceback
import requests
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

from agent.research_agent    import (
    research_topics, research_series, mark_covered, is_fictional,
    normalize_topic_title, extract_canonical_entities,
    classify_topic_domain_with_context, semantic_confidence_score,
)
from agent.script_agent      import write_script, translate_script, generate_chapters, write_short_script, clean_word_count, expand_script_runtime
from agent.animation_agent   import create_animation_video, init_topic_lock
from agent.video_agent       import ensure_music_assets, cut_best_short
from agent.notify_agent      import (
    send_message, send_video_to_telegram, send_daily_report,
    send_english_script_preview, send_arabic_script_preview, send_document,
)
from agent.publish_agent     import upload_to_youtube
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
    if _ctrl is None or not _ctrl.is_cancelled():
        return
    note = f" during {stage}" if stage else ""
    send_message(f"[ANIM] Pipeline cancelled safely{note}.")
    print(f"[ANIM] Cancelled{note} — exiting.")
    if _ctrl:
        _ctrl.stop()
    import gc as _gc
    _gc.collect()
    sys.exit(0)


def _make_animation_video(
    script_data: dict,
    research: dict,
    output_dir: str,
    stats: dict,
    label: str,
) -> str:
    try:
        path = create_animation_video(
            script_data,
            research,
            output_dir=output_dir,
        )
        if path and Path(path).exists():
            stats["generated"] += 1
            _log("AnimGen", f"Ready ({label}): {os.path.basename(path)}", "OK")
            return path
        raise RuntimeError("create_animation_video returned no file")
    except Exception as e:
        traceback.print_exc()
        _log("AnimGen", f"{label}: {type(e).__name__}: {e}", "ERROR")
        send_message(f"[ANIM] Video failed for {label}: {type(e).__name__}: {e}")
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


# ── Topic selection helpers ───────────────────────────────────────────────────

def _normalize_topic_title(title: str) -> str:
    """
    Validate and clean a topic title before display.
    Returns "" if the title is truncated, too short, or malformed.
    """
    if not title:
        return ""
    title = title.strip()
    if len(title) < 15:
        return ""
    # Reject titles that end mid-word (truncated — no terminal punctuation
    # and last word is suspiciously short)
    if title[-1].isalnum():
        last_word = title.split()[-1]
        # A final word of 1–3 chars that isn't a common short word = truncation
        short_ok = {"a", "an", "the", "of", "in", "at", "by", "on", "to", "up",
                    "bc", "ad", "ce", "ad"}
        if len(last_word) <= 4 and last_word.lower() not in short_ok:
            return ""
    return title


def _tg_send(base: str, chat_id: str | int, text: str) -> None:
    """Fire-and-forget Telegram message."""
    try:
        requests.post(
            f"{base}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except Exception as e:
        print(f"[TOPIC] Telegram send failed: {e}")


def _wait_for_topic_selection(
    candidates: list[dict],
    timeout_sec: int = 300,
) -> dict | str | None:
    """
    Send numbered topic candidates to Telegram and wait for user selection.

    Returns:
      - topic dict  if user selects a number or /auto
      - "CANCEL"    if user sends /cancel
      - None        if timeout expires (caller should auto-select)

    Supported replies: 1 / 2 / 3 / 4 · /auto · /cancel · /refresh
    """
    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    except ImportError:
        print("[TOPIC] Telegram config unavailable — skipping selection wait")
        return None

    base    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    chat_id = TELEGRAM_CHAT_ID

    # Normalize and filter candidates
    valid: list[dict] = []
    for c in candidates:
        t = _normalize_topic_title(c.get("topic", ""))
        if t:
            valid.append({**c, "topic": t})

    if not valid:
        print("[TOPIC] No valid candidates after normalization — skipping selection")
        return None

    # Build the numbered menu
    lines = ["[ANIMATION PIPELINE]\nSelect a topic:\n"]
    for i, c in enumerate(valid, 1):
        lines.append(f"{i}. {c['topic']}")
    lines.append(f"{len(valid) + 1}. Auto-select best topic")
    reply_hint = " / ".join(str(i) for i in range(1, len(valid) + 2))
    lines.append(f"\nReply with: {reply_hint}")
    lines.append("Or: /auto · /cancel · /refresh")
    menu_text = "\n".join(lines)

    # Advance the offset so we only see replies AFTER this message
    _offset: int | None = None
    try:
        r = requests.get(
            f"{base}/getUpdates",
            params={"timeout": 0, "limit": 1},
            timeout=10,
        )
        updates = r.json().get("result", [])
        if updates:
            _offset = updates[-1]["update_id"] + 1
    except Exception:
        pass

    _tg_send(base, chat_id, menu_text)
    print(f"[TOPIC] Candidate list generated — {len(valid)} topics sent to Telegram")
    print(f"[TOPIC] Waiting for Telegram selection (timeout: {timeout_sec}s)")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(8)
        remaining = int(deadline - time.time())

        try:
            params: dict = {
                "timeout": 0,
                "limit":   10,
                "allowed_updates": ["message"],
            }
            if _offset is not None:
                params["offset"] = _offset
            r = requests.get(f"{base}/getUpdates", params=params, timeout=15)
            if not r.ok:
                continue
            updates = r.json().get("result", [])
        except Exception:
            continue

        for upd in updates:
            _offset = upd["update_id"] + 1
            msg_obj = upd.get("message", {})
            if str(msg_obj.get("chat", {}).get("id", "")) != str(chat_id):
                continue
            text = (msg_obj.get("text") or "").strip()
            cmd  = text.lower()

            # Cancel
            if cmd in ("/cancel", "cancel"):
                print("[TOPIC] User cancelled via Telegram")
                _tg_send(base, chat_id, "[ANIMATION PIPELINE] Cancelled.")
                return "CANCEL"

            # Auto-select
            if cmd in ("/auto", "auto", str(len(valid) + 1)):
                selected = valid[0]
                print(f"[TOPIC] User requested auto-select → {selected['topic'][:60]}")
                _tg_send(base, chat_id,
                    f"[ANIMATION PIPELINE] Auto-selecting:\n{selected['topic']}\n\nStarting...")
                return selected

            # Re-send menu
            if cmd == "/refresh":
                _tg_send(base, chat_id, menu_text)
                continue

            # Number selection
            try:
                n = int(text)
                if 1 <= n <= len(valid):
                    selected = valid[n - 1]
                    print(f"[TOPIC] User selected topic {n}: {selected['topic'][:60]}")
                    _tg_send(base, chat_id,
                        f"[ANIMATION PIPELINE] Selected:\n{selected['topic']}\n\nStarting generation...")
                    return selected
            except ValueError:
                pass

    # Timeout — let caller decide
    print(f"[TOPIC] No Telegram reply in {timeout_sec}s — timeout")
    return None


# ── Main entry point ─────────────────────────────────────────────────────────

def run_pipeline() -> None:
    global _ctrl
    t0    = time.time()
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    _ctrl = TelegramController(mode="animation")

    print(f"\n{'='*60}")
    print(f"  [ANIMATION PIPELINE] Dark Crime Decoded — {today}")
    print(f"  PIPELINE_MODE = {os.getenv('PIPELINE_MODE','animation')}")
    print(f"  Visual mode: character-centric motion documentary")
    print(f"{'='*60}\n")

    ensure_music_assets()

    # ── STEP 1: Topic selection (Telegram-first) ──────────────────────────────
    print(f"\n{'='*50}\n  TOPIC SELECTION\n{'='*50}\n", flush=True)

    # Allow manual topic override via environment variable
    _manual_topic = os.getenv("ANIM_TOPIC", "").strip()
    _topic_wait_sec = int(os.getenv("ANIM_TOPIC_WAIT_SEC", "300"))

    topic: dict = {}

    if _manual_topic:
        # User pre-supplied the topic — use immediately, no Telegram wait
        _log("Research", f"Manual topic override: '{_manual_topic}'", "OK")
        topic = {"topic": _manual_topic, "niche": _manual_topic}
        _ctrl.set_topic(_manual_topic)
        _ctrl.start()
    else:
        # Auto-discover candidates, let user choose via Telegram
        _ctrl.update_stage("Research", "discovering topic candidates")
        _log("Research", "Discovering topic candidates for Telegram selection")
        try:
            _candidates = research_topics(count=4)
            if not _candidates:
                raise RuntimeError("research_topics returned empty list")
        except Exception as e:
            send_message(f"[ANIM] Topic discovery failed: {e}")
            _log("Research", str(e), "ERROR")
            return

        # Wait for user selection (blocking — up to _topic_wait_sec seconds)
        _selection = _wait_for_topic_selection(_candidates, timeout_sec=_topic_wait_sec)

        if _selection == "CANCEL":
            _log("Research", "Pipeline cancelled via Telegram topic selection", "WARN")
            return

        if _selection is None:
            # Timeout — auto-select best candidate and notify
            topic = _candidates[0]
            _auto_title = topic.get("topic", "")
            print(f"[TOPIC] Auto-selected after timeout: {_auto_title[:60]}")
            send_message(
                f"[ANIMATION PIPELINE]\nNo selection received.\n"
                f"Auto-selecting topic:\n{_auto_title}"
            )
            _log("Research", f"Auto-selected after timeout: '{_auto_title}'", "OK")
        else:
            topic = _selection

        _ctrl.set_topic(topic.get("topic", ""))
        _ctrl.start()

    topic_text  = topic.get("topic", "")
    topic_niche = topic.get("niche", "")

    if is_fictional(topic_text, topic_niche):
        _log("Research", f"Fictional topic blocked: '{topic_text}'", "WARN")
        send_message(f"[ANIM] Fictional topic blocked: '{topic_text}'")
        return

    _log("Research", f"Topic: '{topic_text}'", "OK")

    # Hard reset: clear all identity/character/clip state from any previous run
    init_topic_lock(topic_text)

    # ── Pre-research semantic gate ────────────────────────────────────────────
    _entities   = extract_canonical_entities(topic_text)
    _ctx_domain = classify_topic_domain_with_context(topic_text, _entities)
    _conf       = semantic_confidence_score(topic_text, _entities)

    _log("Research",
         f"Domain: {_ctx_domain} | Confidence: {_conf:.2f} "
         f"({'HIGH' if _conf >= 0.7 else 'MEDIUM' if _conf >= 0.4 else 'LOW'})")

    if _conf < 0.4:
        _msg = (
            f"[ANIM] Topic rejected — confidence too low ({_conf:.2f}):\n"
            f"'{topic_text[:80]}'\n\n"
            f"Pipeline halted before research. Send a clearer topic."
        )
        _log("Research", f"LOW confidence ({_conf:.2f}) — aborting", "ERROR")
        send_message(_msg)
        return

    if _manual_topic:
        send_message(f"[ANIMATION PIPELINE] Topic (manual): {topic_text}\n\nStarting animation generation...")

    # ── STEP 1b: Deep research ────────────────────────────────────────────────
    print(f"\n{'='*50}\n  RESEARCH\n{'='*50}\n", flush=True)

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
    print(f"\n{'='*50}\n  SCRIPTS\n{'='*50}\n", flush=True)
    _ctrl.update_stage("Scripts", "writing English script")
    _log("Scripts", "Writing English script")
    try:
        en_long = write_script(topic, language="english")
    except Exception as e:
        traceback.print_exc()
        send_message(f"[ANIM] Script failed: {type(e).__name__}: {e}")
        _log("Scripts", f"{type(e).__name__}: {e}", "ERROR")
        return

    _en_wc   = clean_word_count(en_long.get("script", ""))
    _est_min = round(_en_wc / WORDS_PER_MINUTE, 1)
    _max_ret = 3
    _retry   = 0

    while _en_wc < SCRIPT_WORD_FLOOR and _retry < _max_ret:
        _missing = SCRIPT_WORD_FLOOR - _en_wc
        _log("Scripts", f"Short by {_missing} words — expansion attempt {_retry + 1}/{_max_ret}", "WARN")
        _expanded = expand_script_runtime(en_long["script"], _missing, topic=en_long.get("topic", ""))
        _new_wc = clean_word_count(_expanded)
        if _new_wc > _en_wc:
            en_long["script"] = _expanded
            _en_wc   = _new_wc
            _est_min = round(_en_wc / WORDS_PER_MINUTE, 1)
        _retry += 1

    if _en_wc < SCRIPT_WORD_FLOOR:
        _msg = (
            f"[ANIM] Script still short after {_retry} expansion attempt(s): "
            f"{_en_wc} words (~{_est_min} min) — hard minimum is {SCRIPT_WORD_FLOOR}. Aborting."
        )
        _log("Scripts", _msg, "ERROR")
        send_message(_msg)
        return

    _log("Scripts", f"Script: {_en_wc} words — Est. runtime: ~{_est_min} min", "OK")

    _ctrl.set_latest_script(en_long)
    _ctrl.update_stage("Scripts", "sending script preview")
    try:
        send_english_script_preview(en_long, label=f"[ANIM] SCRIPT READY — {en_long.get('title','')}")
        _log("Scripts", "English script sent to Telegram", "OK")
    except Exception as _e:
        _log("Scripts", f"Script preview (non-fatal): {_e}", "WARN")

    _script_txt_path = f"output/anim_script_{today}.txt"
    try:
        os.makedirs("output", exist_ok=True)
        with open(_script_txt_path, "w", encoding="utf-8") as _sf:
            _sf.write(
                f"TITLE: {en_long.get('title','')}\n"
                f"TOPIC: {en_long.get('topic','')}\n"
                f"WORDS: {_en_wc}  EST: ~{_est_min} min\n"
                f"MODE: animation (character-centric motion documentary)\n"
                f"{'='*60}\n\n"
                f"{en_long.get('script','')}"
            )
        send_document(_script_txt_path, caption=f"[ANIM] Full script — {en_long.get('title','')[:80]}")
        _log("Scripts", "Script .txt uploaded to Telegram", "OK")
    except Exception as _e:
        _log("Scripts", f"Script .txt upload (non-fatal): {_e}", "WARN")

    _check_cancel("after script generation")

    _ctrl.update_stage("Scripts", "writing Arabic script from research")
    try:
        ar_long = translate_script(en_long, research=topic.get("research", {}))
        ar_wc   = len(ar_long.get("script", "").split())
        if ar_wc > 0:
            ar_long["chapters"] = generate_chapters(
                ar_wc, language="arabic", angle_title=en_long.get("angle_title", "")
            )
        ar_long["angle_title"] = en_long.get("angle_title", "")
        ar_long["angle_hook"]  = en_long.get("angle_hook", "")
        _log("Scripts", "Arabic script done", "OK")
        try:
            send_arabic_script_preview(ar_long)
        except Exception as _ae:
            _log("Scripts", f"Arabic preview (non-fatal): {_ae}", "WARN")
    except Exception as e:
        traceback.print_exc()
        _log("Scripts", f"Arabic script failed (non-fatal): {type(e).__name__}: {e}", "WARN")
        ar_long = dict(en_long)
        ar_long["language"] = "arabic"

    _check_cancel("after all scripts")

    # ── STEP 3: Generate animation videos (EN + AR) ───────────────────────────
    print(f"\n{'='*50}\n  ANIMATION VIDEO GENERATION\n{'='*50}\n", flush=True)
    _log("AnimGen", "Starting character-centric motion documentary generation")
    _log("AnimGen", f"Visual style: {topic_text} — real photo + D-ID portrait + motion clips")

    os.makedirs(FINAL_DIR, exist_ok=True)

    _ctrl.update_stage("AnimGen", "generating EN animation video")
    _log("AnimGen", "Generating EN animation video (character-identity locked)")
    en_long_path = _make_animation_video(en_long, topic.get("research", {}), FINAL_DIR, stats, "EN long")

    _check_cancel("after EN animation render")

    _ctrl.update_stage("AnimGen", "generating AR animation video")
    _log("AnimGen", "Generating AR animation video")

    _ar_wc_check  = len(ar_long.get("script", "").split())
    _ar_min_check = _ar_wc_check / 130.0
    _AR_LONG_MIN  = 5.0
    if ar_long.get("script_too_short") or _ar_min_check < _AR_LONG_MIN:
        _block_msg = (
            f"[AR BLOCKED] Runtime below minimum: {_ar_min_check:.1f}min "
            f"({_ar_wc_check}w) < {_AR_LONG_MIN}min — "
            f"blocking Arabic animation render to prevent invalid upload"
        )
        _log("AnimGen", _block_msg, "ERROR")
        send_message(_block_msg)
        ar_long_path = ""
        stats["errors"] += 1
    else:
        ar_long_path = _make_animation_video(ar_long, topic.get("research", {}), FINAL_DIR, stats, "AR long")

    _check_cancel("after AR animation render")

    # ── STEP 4: Promo shorts (cut from long animation videos) ─────────────────
    # Animation pipeline does not re-render a separate short script —
    # the motion clips are already the best possible visuals. We cut the
    # strongest moment from the finished long animation video instead.
    print(f"\n{'='*50}\n  SHORTS\n{'='*50}\n", flush=True)
    _ctrl.update_stage("Shorts", "cutting EN promo short from animation")

    en_short_path = ""
    ar_short_path = ""

    if en_long_path and os.path.exists(en_long_path):
        _log("Shorts", "Cutting EN short from animation video")
        try:
            _cuts = cut_best_short(en_long_path, en_long)
            en_short_path = _cuts[0]["path"] if _cuts else ""
            if en_short_path:
                _log("Shorts", f"EN short ready: {os.path.basename(en_short_path)}", "OK")
            else:
                _log("Shorts", "EN short cut returned no clip", "WARN")
        except Exception as _ce:
            _log("Shorts", f"EN short cut failed: {_ce}", "ERROR")
    else:
        _log("Shorts", "EN long video missing — cannot cut short", "WARN")

    _ctrl.update_stage("Shorts", "cutting AR promo short from animation")
    if ar_long_path and os.path.exists(ar_long_path):
        _log("Shorts", "Cutting AR short from animation video")
        try:
            _cuts = cut_best_short(ar_long_path, ar_long)
            ar_short_path = _cuts[0]["path"] if _cuts else ""
            if ar_short_path:
                _log("Shorts", f"AR short ready: {os.path.basename(ar_short_path)}", "OK")
            else:
                _log("Shorts", "AR short cut returned no clip", "WARN")
        except Exception as _ce:
            _log("Shorts", f"AR short cut failed: {_ce}", "ERROR")
    else:
        _log("Shorts", "AR long video missing — cannot cut short", "WARN")

    # ── STEP 5: Publish ───────────────────────────────────────────────────────
    print(f"\n{'='*50}\n  UPLOAD\n{'='*50}\n", flush=True)
    _ctrl.update_stage("Publish", "uploading to YouTube")
    _run_id       = os.getenv("GITHUB_RUN_ID", "")
    _repo         = os.getenv("GITHUB_REPOSITORY", "abdotaj/AI_Content_Pipeline")
    _artifact_url = f"https://github.com/{_repo}/actions/runs/{_run_id}" if _run_id else ""

    yt_en_url = ""
    if en_long_path and os.path.exists(en_long_path):
        try:
            yt_en_url = upload_to_youtube(en_long_path, en_long, token_file=YOUTUBE_TOKEN_FILE_EN)
            if yt_en_url:
                send_message(f"[ANIM] EN video live: {yt_en_url}")
                _log("Publish", f"EN: {yt_en_url}", "OK")
            else:
                _log("Publish", "EN upload returned empty URL", "ERROR")
                send_message(f"[ANIM] EN upload failed{f' — {_artifact_url}' if _artifact_url else ''}")
                stats["errors"] += 1
        except Exception as e:
            _log("Publish", f"EN upload exception: {e}", "ERROR")
            send_message(f"[ANIM] EN upload error: {e}")
            stats["errors"] += 1

    yt_ar_url = ""
    if ar_long_path and os.path.exists(ar_long_path):
        try:
            yt_ar_url = upload_to_youtube(ar_long_path, ar_long, token_file=YOUTUBE_TOKEN_FILE_AR)
            if yt_ar_url:
                send_message(f"[ANIM] AR video live: {yt_ar_url}")
                _log("Publish", f"AR: {yt_ar_url}", "OK")
            else:
                _log("Publish", "AR upload returned empty URL", "ERROR")
                send_message(f"[ANIM] AR upload failed{f' — {_artifact_url}' if _artifact_url else ''}")
                stats["errors"] += 1
        except Exception as e:
            _log("Publish", f"AR upload exception: {e}", "ERROR")
            send_message(f"[ANIM] AR upload error: {e}")
            stats["errors"] += 1

    # ── Send promo shorts to Telegram ─────────────────────────────────────────
    _ctrl.update_stage("Shorts", "uploading promo shorts to Telegram")
    _shorts_sent = 0
    for short_path, lang_label, script, yt_url in [
        (en_short_path, "EN", en_long, yt_en_url),
        (ar_short_path, "AR", ar_long, yt_ar_url),
    ]:
        if not (short_path and os.path.exists(short_path)):
            _log("Shorts", f"{lang_label} promo short missing — not sent to Telegram", "ERROR")
            send_message(f"[ANIM] {lang_label} promo short missing — not uploaded")
            continue
        _log("Shorts", f"Uploading {lang_label} teaser clip: {os.path.basename(short_path)}")
        try:
            _doc_link = f"\n\nFull documentary: {yt_url}" if yt_url else ""
            caption = (
                f"PROMO SHORT — POST NOW\n\n"
                f"{script.get('title','')}\n"
                f"Platforms: TikTok + Instagram Reels + YouTube Shorts\n"
                f"{script.get('hashtags','')}"
                f"{_doc_link}"
            )
            send_video_to_telegram(short_path, caption, f"{lang_label} Promo Short")
            _log("Shorts", f"{lang_label} promo video complete: {os.path.basename(short_path)}", "OK")
            _shorts_sent += 1
        except Exception as e:
            _log("Shorts", f"{lang_label} promo short send failed: {e}", "WARN")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*50}\n  SUMMARY\n{'='*50}\n", flush=True)
    elapsed = (time.time() - t0) / 60
    _status_en     = f"OK: {yt_en_url}" if yt_en_url else "FAILED"
    _status_ar     = f"OK: {yt_ar_url}" if yt_ar_url else "FAILED"
    _short_en_file = os.path.basename(en_short_path) if en_short_path else "MISSING"
    _short_ar_file = os.path.basename(ar_short_path) if ar_short_path else "MISSING"
    send_message(
        f"[ANIMATION PIPELINE] Done — {today}\n\n"
        f"Time: {elapsed:.0f} min\n"
        f"Visual mode: character-centric motion documentary\n\n"
        f"DOCUMENTARIES\n"
        f"  EN: {_status_en}\n"
        f"  AR: {_status_ar}\n\n"
        f"SHORTS\n"
        f"  EN: {'OK' if en_short_path else 'FAILED'} — {_short_en_file}\n"
        f"  AR: {'OK' if ar_short_path else 'FAILED'} — {_short_ar_file}\n"
        f"  Sent: {_shorts_sent}/2"
    )

    series_name = en_long.get("series") or en_long.get("niche", "").split("behind")[-1].strip()
    if series_name:
        try:
            mark_covered(series_name, f"{today}_anim")
        except Exception:
            pass

    _ctrl.update_stage("Done")
    _ctrl.stop()

    send_daily_report(stats)
    _result = "SUCCESS" if stats["errors"] == 0 else f"PARTIAL ({stats['errors']} error(s))"
    print(f"\n{'='*60}")
    print(f"  [ANIM] {_result} — {today}  ({elapsed:.0f} min)")
    print(f"  Generated: {stats['generated']} | Errors: {stats['errors']}")
    print(f"{'='*60}\n")
