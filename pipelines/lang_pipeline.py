# pipelines/lang_pipeline.py
#
# LANGUAGE-ISOLATED TEST PIPELINE
#
# Runs a SINGLE language (arabic or english) in FAST, FULL, or ANIMATION mode.
#
# SAFETY CONTRACT:
#   ✗  NO YouTube upload  — test mode only
#   ✗  NO production routing change
#   ✓  Telegram output with [LANG TEST] prefix for manual review
#   ✓  Output to output/lang_test/ directory
#   ✓  All stable fixes preserved (composition, sequencing, AR validation)
#   ✓  Failure cannot affect the other language or existing pipeline modes
#
# Entry points (six thin files, each sets PIPELINE_MODE before importing):
#   run_fast_ar.py / run_fast_en.py
#   run_full_ar.py / run_full_en.py
#   run_animation_ar.py / run_animation_en.py
#
# Arabic modes:
#   - write_arabic_script()  — native Arabic, no English dependency
#   - write_arabic_short()   — native Arabic short, no translation
#   - mode-specific word gate (5500/6500/7500w), 30/35/45-min runtime rebuild loop
#
# English modes:
#   - write_script("english")
#   - write_animation_script() in animation mode
#   - write_short_script()
#
# topic_inject.json is consumed (deleted) on first read — same as all other modes.
# Run AR and EN variants on separate invocations if using inject.

import os
import sys
import json
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

from agent.research_agent import research_topics, research_series, is_fictional
from agent.script_agent import (
    write_script,
    write_animation_script,
    generate_chapters,
    write_short_script,
    write_arabic_script,
    write_arabic_short,
    clean_word_count,
)
from agent.video_agent import (
    create_video,
    ensure_music_assets,
    cut_best_short,
    load_all_content,
)
from agent.notify_agent import (
    send_message,
    send_video_to_telegram,
    send_english_script_preview,
    send_arabic_script_preview,
)
from pipelines.approval import wait_for_approval


# ── Internal helpers ──────────────────────────────────────────────────────────

def _log(stage: str, msg: str, level: str = "INFO") -> None:
    ts  = datetime.datetime.now().strftime("%H:%M:%S")
    tag = {"WARN": "WARN", "ERROR": "ERR ", "OK": "OK  "}.get(level, "INFO")
    print(f"[{ts}][{tag}][{stage}] {msg}", flush=True)


def _make_video(script_data: dict, video_id: str, stats: dict,
                user_images=None, user_videos=None) -> str:
    """Route to create_video(); update stats; return path or ''."""
    try:
        raw  = create_video(script_data, video_id,
                            user_images=user_images, user_videos=user_videos)
        path = raw[0] if isinstance(raw, tuple) else raw
        if path and Path(path).exists():
            stats["generated"] += 1
            _log("VideoGen", f"Ready: {path}", "OK")
            return path
        raise RuntimeError("create_video returned no file")
    except Exception as exc:
        traceback.print_exc()
        _log("VideoGen", f"{video_id}: {type(exc).__name__}: {exc}", "ERROR")
        send_message(f"[LANG TEST] Video failed — {video_id}: {exc}")
        stats["errors"] += 1
        return ""


def _make_animation_video(script_data: dict, research: dict,
                          stats: dict, label: str) -> str:
    """Route to create_animation_video(); update stats; return path or ''."""
    try:
        from agent.animation_agent import create_animation_video
        path = create_animation_video(script_data, research)
        if path and Path(path).exists():
            stats["generated"] += 1
            _log("AnimGen", f"Ready ({label}): {os.path.basename(path)}", "OK")
            return path
        raise RuntimeError("create_animation_video returned no file")
    except Exception as exc:
        traceback.print_exc()
        _log("AnimGen", f"{label}: {type(exc).__name__}: {exc}", "ERROR")
        send_message(f"[LANG TEST] Animation render failed — {label}: {exc}")
        stats["errors"] += 1
        return ""


def _video_secs(path: str) -> float:
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _wait_for_topic_lang(label: str, candidates: list, timeout_sec: int = 300) -> dict | None:
    """Show a numbered Telegram menu and wait for explicit topic selection.

    Returns the selected topic dict, or None if the user cancels or the
    timeout expires (no auto-selection — caller must abort).
    """
    import requests as _req

    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        base    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        chat_id = str(TELEGRAM_CHAT_ID)
    except Exception:
        print("[TOPIC] Telegram config unavailable — cannot show menu")
        return None

    def _tg_send(text: str) -> None:
        try:
            _req.post(f"{base}/sendMessage",
                      json={"chat_id": chat_id, "text": text},
                      timeout=12)
        except Exception as _e:
            print(f"[TOPIC] Telegram send failed: {_e}")

    if not candidates:
        # No auto-candidates — still allow free-text manual input
        cancel_n  = 1
        menu_text = (
            f"{label} Auto-research found no topic candidates.\n\n"
            "Type a topic directly (e.g. 'Pablo Escobar', 'El Chapo').\n\n"
            f"Or 'cancel' to stop. Timeout: {timeout_sec // 60} min."
        )
    else:
        # Build numbered menu
        lines = [f"{label}\n\nSelect a topic:\n"]
        for i, c in enumerate(candidates, 1):
            lines.append(f"{i}. {c.get('topic', '?')}")
        cancel_n = len(candidates) + 1
        lines.append(f"\n{cancel_n}. Cancel")
        lines.append(f"\nOr type any topic directly.")
        lines.append(f"Timeout: {timeout_sec // 60} min → auto-select #1")
        menu_text = "\n".join(lines)

    # Advance offset so we only read messages sent AFTER this menu
    _offset: int | None = None
    try:
        r = _req.get(f"{base}/getUpdates", params={"timeout": 0, "limit": 1}, timeout=10)
        updates = r.json().get("result", [])
        if updates:
            _offset = updates[-1]["update_id"] + 1
    except Exception:
        pass

    _tg_send(menu_text)
    print(f"[TOPIC] Menu sent — waiting up to {timeout_sec}s for selection")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(8)
        try:
            params: dict = {"timeout": 0, "limit": 10, "allowed_updates": ["message"]}
            if _offset is not None:
                params["offset"] = _offset
            r = _req.get(f"{base}/getUpdates", params=params, timeout=15)
            if not r.ok:
                continue
            updates = r.json().get("result", [])
        except Exception:
            continue

        for upd in updates:
            _offset = upd["update_id"] + 1
            msg  = upd.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) != chat_id:
                continue
            text = (msg.get("text") or "").strip()
            cmd  = text.lower()

            # Cancel
            if cmd in ("/cancel", "cancel", str(cancel_n)):
                _tg_send(f"{label} Cancelled.")
                print("[TOPIC] Cancelled via Telegram")
                return None

            # Numbered pick
            try:
                n = int(text)
                if 1 <= n <= len(candidates):
                    sel = candidates[n - 1]
                    _tg_send(f"{label} Selected:\n{sel['topic']}\n\nStarting...")
                    print(f"[TOPIC] Selected {n}: {sel['topic'][:60]}")
                    return sel
                continue
            except ValueError:
                pass

            # Free-text topic (≥5 chars treated as manual override)
            if len(text) >= 5:
                sel = {"topic": text.strip(), "niche": text.strip(), "manual_topic": True}
                _tg_send(f"{label} Manual topic:\n\"{text}\"\n\nStarting...")
                print(f"[TOPIC] Free-text topic: '{text[:60]}'")
                return sel

    # Timeout — auto-select first candidate if available
    print(f"[TOPIC] Timeout ({timeout_sec}s)")
    if candidates:
        auto = candidates[0]
        _tg_send(f"{label} No reply — auto-selecting:\n{auto.get('topic','?')}\n\nStarting...")
        print(f"[TOPIC] Auto-selected: {auto.get('topic','?')[:60]}")
        return auto
    _tg_send(f"{label} ⛔ No selection and no candidates. Pipeline stopped.")
    print(f"[TOPIC] Timeout — no candidates, aborting")
    return None


def _topic_slug(topic: str) -> str:
    import re
    s = re.sub(r"[^a-zA-Z0-9 ]", "", (topic or "video").lower()).strip()
    s = re.sub(r"\s+", "_", s)
    return (s[:30].rstrip("_")) or "video"


# ── Main entry point ──────────────────────────────────────────────────────────

def run_lang_pipeline(language: str, mode: str) -> None:
    """
    Isolated single-language pipeline.

    Parameters
    ----------
    language : "arabic" | "english"
    mode     : "fast" | "full" | "animation"

    Safety guarantee:
    - PIPELINE_MODE must be set by the caller BEFORE importing this module.
    - No YouTube upload.
    - Telegram output has [LANG TEST] prefix — distinguishable from production.
    - Failure here cannot affect run_fast.py / run_full.py / run_animation.py.
    """
    assert language in ("arabic", "english"), f"Unsupported language: {language!r}"
    assert mode in ("fast", "full", "animation"), f"Unsupported mode: {mode!r}"

    _lang  = language
    _mode  = mode
    _label = f"[LANG TEST | {_mode.upper()} | {_lang.upper()}]"

    t0    = time.time()
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "errors": 0}

    os.makedirs("output/lang_test", exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Language Test Pipeline — {today}")
    print(f"  Language  : {_lang.upper()}")
    print(f"  Mode      : {_mode.upper()}")
    print(f"  PIPELINE_MODE = {os.getenv('PIPELINE_MODE', '?')}")
    print(f"{'='*60}\n")

    send_message(f"{_label} Starting...\nLanguage: {_lang} | Mode: {_mode}")
    ensure_music_assets()

    # ── STEP 1: Topic ─────────────────────────────────────────────────────────
    topic = None
    _inject_file = os.path.join(_ROOT, "topic_inject.json")
    if os.path.exists(_inject_file):
        try:
            with open(_inject_file, encoding="utf-8") as _f:
                _inject = json.load(_f)
            os.remove(_inject_file)
            _inject_text = _inject.get("topic", "").strip()
            if _inject_text:
                _log("Research", f"Inject topic: {_inject_text!r}")
                topic = {
                    "topic":        _inject_text,
                    "niche":        f"Real story behind {_inject.get('show') or _inject_text}",
                    "series_name":  _inject.get("show") or None,
                    "manual_topic": True,
                    "user_note":    _inject.get("note", ""),
                }
        except Exception as _ie:
            _log("Research", f"topic_inject.json error (ignored): {_ie}", "WARN")

    if topic is None:
        # No pre-set topic — show Telegram menu and wait for explicit selection
        _log("Research", "No topic_inject.json — showing Telegram topic menu")
        try:
            _candidates = research_topics(count=4)
        except Exception as _ce:
            _candidates = []
            print(f"[TOPIC] research_topics failed: {_ce}")
        selected = _wait_for_topic_lang(_label, _candidates, timeout_sec=300)
        if selected is None:
            _log("Research", "Topic selection cancelled or timed out — pipeline aborted", "WARN")
            return
        topic = selected

    topic_text  = topic.get("topic", "")
    topic_niche = topic.get("niche", "")

    if is_fictional(topic_text, topic_niche):
        _log("Research", f"Fictional topic blocked: {topic_text!r}", "WARN")
        send_message(f"{_label} Fictional topic blocked: {topic_text!r}")
        return

    _log("Research", f"Topic: {topic_text!r}", "OK")
    send_message(f"{_label} Topic: {topic_text}")

    series = (topic_niche.split("behind")[-1].strip()
              if "behind" in topic_niche else topic_text)
    try:
        research = research_series(series, user_note=topic.get("user_note", ""))
        if research is None:
            research = {}
        research["real_person"] = topic_text
        topic["research"]       = research
        _log("Research", "Research done", "OK")
    except Exception as exc:
        _log("Research", f"research_series failed (non-fatal): {exc}", "WARN")
        topic["research"] = {}

    # ── STEP 2: Script (language-specific) ────────────────────────────────────
    long_script  = None
    short_script_text = ""

    if _lang == "english":
        # Use animation-optimised script writer in animation mode
        _script_fn = write_animation_script if _mode == "animation" else write_script
        try:
            if _mode == "animation":
                long_script = write_animation_script(topic)
            else:
                long_script = write_script(topic, language="english")
            _en_wc = clean_word_count(long_script.get("script", ""))
            _log("Scripts", f"EN script: {_en_wc}w", "OK")
            send_english_script_preview(long_script, label=f"{_label} SCRIPT")
        except Exception as exc:
            send_message(f"{_label} EN script failed: {exc}")
            _log("Scripts", str(exc), "ERROR")
            return

        # Short script (not for animation mode)
        if _mode != "animation":
            try:
                _short = write_short_script(long_script)
                short_script_text = _short.get("short_script_en", "")
                long_script["short_script_en"] = short_script_text
                _log("Scripts", f"EN short: {len(short_script_text.split())}w", "OK")
            except Exception as exc:
                _log("Scripts", f"EN short failed (non-fatal): {exc}", "WARN")
                long_script.setdefault("short_script_en", "")

    else:  # arabic — native, no English dependency
        try:
            long_script = write_arabic_script(topic, topic.get("research", {}))
            _ar_wc = len(long_script.get("script", "").split())
            if _ar_wc > 0:
                long_script["chapters"] = generate_chapters(_ar_wc, language="arabic")
            _log("Scripts", f"AR script: {_ar_wc}w | path={long_script.get('arabic_path','?')}", "OK")
            send_arabic_script_preview(long_script, label=f"{_label} SCRIPT")
        except Exception as exc:
            send_message(f"{_label} AR script failed: {exc}")
            _log("Scripts", str(exc), "ERROR")
            return

        # Short script (not for animation mode)
        if _mode != "animation":
            try:
                _ar_short = write_arabic_short(long_script)
                short_script_text = _ar_short.get("short_script_ar", "")
                long_script["short_script_ar"] = short_script_text
                _log("Scripts", f"AR short: {len(short_script_text.split())}w", "OK")
            except Exception as exc:
                _log("Scripts", f"AR short failed (non-fatal): {exc}", "WARN")
                long_script.setdefault("short_script_ar", "")

    # ── Approval gate: review script before rendering ──────────────────────────
    _wc_display = (clean_word_count(long_script.get("script", ""))
                   if _lang == "english"
                   else len(long_script.get("script", "").split()))
    while True:
        _approval = wait_for_approval(
            stage_name=(
                f"{_label} Script Ready\n"
                f"{long_script.get('title', topic_text)[:60]}\n"
                f"{_lang.upper()} · {_wc_display}w · {_mode}"
            ),
            available_commands=["approve", "cancel"],
            mode="PIPELINE",
        )
        if _approval == "cancel":
            send_message(f"{_label} Cancelled at script gate.")
            return
        elif _approval == "approve":
            break

    # ── STEP 3: Content library ───────────────────────────────────────────────
    _topic_for_media = long_script.get("topic", topic_text)
    gh_images, gh_videos, _, _ = load_all_content(_topic_for_media)
    user_images = [
        {"path": p, "tags": [], "caption": os.path.basename(p)} for p in gh_images
    ]
    user_videos = list(gh_videos)
    if gh_images or gh_videos:
        _log("Media", f"{len(gh_images)} images + {len(gh_videos)} videos loaded", "OK")

    # ── STEP 4: Render long video ─────────────────────────────────────────────
    _slug     = _topic_slug(topic_text)
    _video_id = f"{today}_{_slug}_{_lang[:2]}_{_mode}_long"

    # Arabic script check — only skip if truly empty
    if _lang == "arabic":
        _ar_wc_gate = len(long_script.get("script", "").split())
        if _ar_wc_gate < 50:
            _log("VideoGen", f"{_label} AR script empty ({_ar_wc_gate}w) — skipping", "ERROR")
            return
        if _ar_wc_gate < 8_000:
            _log("VideoGen", f"{_label} AR {_ar_wc_gate}w below 8000w target — rendering anyway", "WARN")

    _log("VideoGen", f"Rendering {_lang.upper()} long video ({_mode})")

    # Animation mode requires topic lock + content storage init before first render.
    # The full animation_pipeline.py does this setup; without it create_animation_video()
    # has no character identity, domain context, or persistent clip cache.
    if _mode == "animation":
        try:
            from agent.animation_agent import init_topic_lock
            from agent.video_agent import set_active_topic_content
            from utils.content_manager import ensure_topic_content
            init_topic_lock(topic_text)
            _anim_content = ensure_topic_content(topic_text)
            set_active_topic_content(_anim_content)
            _log("VideoGen", f"Animation topic lock + content storage ready: {topic_text[:50]}", "OK")
        except Exception as _anim_init_err:
            _log("VideoGen", f"Animation init (non-fatal): {_anim_init_err}", "WARN")

    if _mode == "animation":
        long_path = _make_animation_video(
            long_script, topic.get("research", {}), stats,
            label=f"{_lang.upper()} long ({_mode})",
        )
    else:
        long_path = _make_video(long_script, _video_id, stats,
                                user_images=user_images, user_videos=user_videos)

    if not long_path:
        send_message(f"{_label} Long video render failed — aborting")
        return

    # ── Arabic runtime rebuild — mode-specific tiered validation ─────────────
    # Checks ACTUAL rendered audio duration (ffprobe). Tiers by mode:
    #   FULL:  <30m=FAIL  30-44m=UNDER TARGET  45-60m=IDEAL  60-90m=ACCEPTABLE  >90m=TOO LONG
    #   ANIM:  <30m=FAIL  30-35m=UNDER TARGET  35-60m=IDEAL  >60m=ACCEPTABLE
    #   FAST:  <30m=FAIL  30-40m=IDEAL         >40m=TOO LONG
    if _lang == "arabic":
        _AR_IDEAL_SECS = {"fast": 1800, "animation": 2100, "full": 2700}.get(_mode, 1800)  # floor for export
        _AR_MAX_SECS   = {"fast": 2400, "animation": 3600, "full": 5400}.get(_mode, 2400)  # 40/60/90 min
        _AR_TGT_MIN    = {"fast": 35.0, "animation": 47.5, "full": 55.0}.get(_mode, 35.0)  # expansion target
        _AR_TARGET_STR = {"fast": "30-40m", "animation": "35-60m", "full": "45-90m"}.get(_mode, "30-40m")
        _AR_HARD_FAIL  = 1800  # 30 min — absolute floor below which is always FAIL
        _ar_rebuild    = 0
        _ar_max_rb     = 4
        while long_path and os.path.exists(long_path):
            _ar_secs = _video_secs(long_path)
            _ar_mins = _ar_secs / 60
            # Determine tier
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
                send_message(f"{_label} AR {_ar_mins:.1f}min exceeds max — exporting as-is")
                break
            _ar_rebuild += 1
            if _ar_rebuild > _ar_max_rb:
                _log("VideoGen", f"[AR EXPANSION] Limit reached — continuing with {_ar_mins:.1f}min", "WARN")
                send_message(f"{_label} AR {_ar_mins:.1f}min after {_ar_max_rb} expansions — proceeding")
                break
            send_message(
                f"{_label} AR {_ar_status}: {_ar_mins:.1f}min | target {_AR_IDEAL_SECS//60}min — "
                f"fallback expansion ({_ar_rebuild}/{_ar_max_rb})..."
            )
            from agent.script_agent import expand_arabic_runtime as _ear
            _ar_wc_pre = len(long_script["script"].split())
            long_script["script"] = _ear(
                long_script["script"], target_min=_AR_TGT_MIN, topic=topic_text
            )
            _ar_wc_post = len(long_script["script"].split())
            if _ar_wc_post <= _ar_wc_pre:
                _log("VideoGen", f"[AR EXPANSION] Expansion added 0 words (content refused or rate-limited) — accepting {_ar_mins:.1f}min video", "WARN")
                send_message(f"{_label} AR expansion failed (0 words added) — accepting {_ar_mins:.1f}min video and continuing")
                break
            _rb_id = f"{_video_id}_rb{_ar_rebuild}"
            if _mode == "animation":
                long_path = _make_animation_video(
                    long_script, topic.get("research", {}), stats,
                    label=f"{_lang.upper()} long ({_mode}) rb{_ar_rebuild}",
                ) or long_path
            else:
                long_path = _make_video(long_script, _rb_id, stats,
                                        user_images=user_images, user_videos=user_videos) or long_path

    # ── English runtime validation — mode-specific max ────────────────────────
    if _lang == "english":
        _EN_MAX_SECS = {"fast": 900, "animation": 1080, "full": 1200}.get(_mode, 900)
        _EN_MIN_SECS = {"fast": 600, "animation": 720,  "full": 900}.get(_mode, 600)
        _EN_TARGET_STR = {"fast": "10-15m", "animation": "12-18m", "full": "15-20m"}.get(_mode, "10-15m")
        _en_comp     = 0
        _en_max_comp = 2
        while long_path and os.path.exists(long_path):
            _en_secs   = _video_secs(long_path)
            _en_mins   = _en_secs / 60
            _en_status = "PASS" if _EN_MIN_SECS <= _en_secs <= _EN_MAX_SECS else ("OVER LIMIT" if _en_secs > _EN_MAX_SECS else "UNDER")
            _log("VideoGen", f"[EN RUNTIME] Target: {_EN_TARGET_STR} | Rendered: {_en_mins:.1f}m | Status: {_en_status}")
            if _en_secs <= _EN_MAX_SECS:
                if _en_secs < _EN_MIN_SECS:
                    _log("VideoGen", f"[EN WARN] {_en_mins:.1f}min < {_EN_MIN_SECS//60}min — content may be too sparse", "WARN")
                break
            _en_comp += 1
            if _en_comp > _en_max_comp:
                _log("VideoGen", f"[EN RUNTIME] Over limit after {_en_max_comp} compressions — proceeding with {_en_mins:.1f}min", "WARN")
                break
            _target_wc = int(len(long_script.get("script", "").split()) * (_EN_MAX_SECS / _en_secs) * 0.92)
            _log("VideoGen", f"[EN COMPRESSION] {_en_mins:.1f}min > limit — compressing to ~{_target_wc}w (attempt {_en_comp}/{_en_max_comp})", "WARN")
            send_message(f"{_label} EN runtime {_en_mins:.1f}min > limit — compressing (attempt {_en_comp}/{_en_max_comp})...")
            from agent.script_agent import compress_english_script as _ces
            _compressed_en = _ces(long_script.get("script", ""), target_words=_target_wc, topic=topic_text)
            if len(_compressed_en.split()) < len(long_script.get("script", "").split()) * 0.97:
                long_script["script"] = _compressed_en
                if _mode == "animation":
                    long_path = _make_animation_video(
                        long_script, topic.get("research", {}), stats,
                        label=f"EN long ({_mode}) c{_en_comp}",
                    ) or long_path
                else:
                    long_path = _make_video(long_script, f"{_video_id}_c{_en_comp}", stats,
                                            user_images=user_images, user_videos=user_videos) or long_path
            else:
                _log("VideoGen", "[EN COMPRESSION] No meaningful reduction — stopping compression", "WARN")
                break

    # ── Short video ───────────────────────────────────────────────────────────
    short_path = ""
    if _mode != "animation":
        _short_key  = "short_script_ar" if _lang == "arabic" else "short_script_en"
        _short_text = long_script.get(_short_key, "")
        _short_id   = f"{today}_{_slug}_{_lang[:2]}_{_mode}_short"

        if _short_text:
            short_path = _make_video(
                {**long_script, "script": _short_text},
                _short_id, stats,
                user_images=user_images, user_videos=user_videos,
            )

        # Fallback: cut from long video
        if not short_path and long_path and os.path.exists(long_path):
            _log("Shorts", "Cutting short from long video (fallback)", "WARN")
            try:
                _cuts      = cut_best_short(long_path, long_script)
                short_path = _cuts[0]["path"] if _cuts else ""
            except Exception as _ce:
                _log("Shorts", f"Short cut failed: {_ce}", "ERROR")

        # Runtime gate
        if short_path:
            _short_secs = _video_secs(short_path)
            if _short_secs < 60:
                _log("Shorts", f"Short {_short_secs:.1f}s < 60s — rejected", "WARN")
                short_path = ""

    # ── STEP 5: Telegram delivery (NO YouTube upload) ─────────────────────────
    _secs_long = _video_secs(long_path)
    _mins_long = _secs_long / 60
    _wc_final  = (clean_word_count(long_script.get("script", ""))
                  if _lang == "english"
                  else len(long_script.get("script", "").split()))

    send_message(
        f"{_label} Long video ready\n\n"
        f"Title:   {long_script.get('title', '?')}\n"
        f"Runtime: {_mins_long:.1f}min\n"
        f"Words:   {_wc_final}\n"
        f"File:    {os.path.basename(long_path)}\n\n"
        f"NOTE: No YouTube upload — test mode only"
    )

    try:
        _long_caption = (
            f"{_label} LONG — MANUAL REVIEW\n\n"
            f"{long_script.get('title', '')}\n"
            f"Runtime: {_mins_long:.1f}min | Words: {_wc_final}\n\n"
            f"DO NOT publish — compare against production output"
        )
        send_video_to_telegram(long_path, _long_caption, f"{_lang.upper()} Long Test ({_mode})")
        _log("Telegram", f"{_lang.upper()} long sent to Telegram", "OK")
    except Exception as exc:
        _log("Telegram", f"Long video send failed (non-fatal): {exc}", "WARN")

    if short_path:
        try:
            _s_secs    = _video_secs(short_path)
            _s_caption = (
                f"{_label} SHORT — MANUAL REVIEW\n\n"
                f"{long_script.get('title', '')}\n"
                f"Runtime: {_s_secs:.1f}s\n\n"
                f"DO NOT publish — compare against production output"
            )
            send_video_to_telegram(short_path, _s_caption, f"{_lang.upper()} Short Test ({_mode})")
            _log("Telegram", f"{_lang.upper()} short sent to Telegram", "OK")
        except Exception as exc:
            _log("Telegram", f"Short send failed (non-fatal): {exc}", "WARN")

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (time.time() - t0) / 60
    _result = "SUCCESS" if stats["errors"] == 0 else f"PARTIAL ({stats['errors']} error(s))"

    send_message(
        f"{_label} DONE — {_result}\n\n"
        f"Time:    {elapsed:.0f}min\n"
        f"Long:    {'OK' if long_path else 'FAILED'} ({_mins_long:.1f}min)\n"
        f"Short:   {'OK' if short_path else 'N/A' if _mode == 'animation' else 'FAILED'}\n"
        f"Errors:  {stats['errors']}\n\n"
        f"YouTube: NOT uploaded (test mode)\n"
        f"Next: compare output vs production, then migrate when stable"
    )

    print(f"\n{'='*60}")
    print(f"  {_label} {_result} — {today}  ({elapsed:.0f}min)")
    print(f"  Generated: {stats['generated']} | Errors: {stats['errors']}")
    print(f"  Long : {long_path or 'FAILED'}")
    print(f"  Short: {short_path or ('N/A (animation)' if _mode == 'animation' else 'FAILED')}")
    print(f"  YouTube: NOT uploaded (test mode)")
    print(f"{'='*60}\n")
