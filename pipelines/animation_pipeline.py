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
import json
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
    entity_confidence_score, build_canonical_research_payload,
    repair_research_payload,
)
from agent.script_agent      import write_script, write_animation_script, translate_script, generate_chapters, write_short_script, clean_word_count, expand_script_runtime, _expand_arabic_script_to_min
from agent.animation_agent   import create_animation_video, init_topic_lock
from agent.video_agent       import ensure_music_assets, cut_best_short, set_active_topic_content
from utils.content_manager   import ensure_topic_content, save_topic_metadata
from agent.notify_agent      import (
    send_message, send_video_to_telegram, send_daily_report,
    send_english_script_preview, send_arabic_script_preview, send_document,
)
from agent.publish_agent     import upload_to_youtube
from pipelines.pipeline_config import SCRIPT_WORD_FLOOR, SCRIPT_WORD_MIN, WORDS_PER_MINUTE
from pipelines.telegram_control import TelegramController, CANCEL_FLAG
from pipelines.approval import wait_for_approval


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
    research = repair_research_payload(
        script_data.get("topic", ""),
        research,
        manual=bool(script_data.get("manual_topic")),
    )
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


def _make_standalone_short(
    short_data: dict,
    research: dict,
    output_dir: str,
    stats: dict,
    label: str,
    language: str = "english",
) -> str:
    if not short_data:
        return ""
    _log("Shorts", "[SHORT PATH A] Independent render active")
    data = dict(short_data)
    data["is_short"] = True
    data["language"] = language
    if language == "arabic":
        data["script"] = data.get("short_script_ar") or data.get("script", "")
        data["topic"] = f"{data.get('topic', '')} short ar".strip()
    else:
        data["script"] = data.get("short_script_en") or data.get("script", "")
        data["topic"] = f"{data.get('topic', '')} short en".strip()
    if not data.get("script"):
        _log("Shorts", f"{label}: no standalone script available", "WARN")
        return ""
    return _make_animation_video(data, research, output_dir, stats, label)


def get_duration(video_path: str) -> str:
    try:
        from moviepy import VideoFileClip
        clip = VideoFileClip(video_path)
        d    = clip.duration
        clip.close()
        return f"{int(d // 60)}:{int(d % 60):02d}"
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


# ── Topic selection helpers ───────────────────────────────────────────────────

import re as _re_pipeline
_SLUG_PAT_PIPE = _re_pipeline.compile(r'^[a-z][a-z0-9]*(?:[_\-][a-z0-9]+)+$')

_TYPE_HASHTAGS: dict[str, list[str]] = {
    "serial killer":    ["#TrueCrime", "#SerialKiller", "#Documentary"],
    "cartel":           ["#TrueCrime", "#Cartel", "#DrugWar"],
    "mafia":            ["#TrueCrime", "#Mafia", "#Gangster"],
    "fraud":            ["#TrueCrime", "#Fraud", "#WhiteCollarCrime"],
    "cult leader":      ["#TrueCrime", "#Cult", "#CriminalMind"],
    "unsolved":         ["#TrueCrime", "#Unsolved", "#ColdCase"],
    "gangster":         ["#TrueCrime", "#Gangster", "#CriminalHistory"],
    "drug trafficking": ["#TrueCrime", "#DrugTrafficking", "#Cartel"],
    "domestic terrorism":["#TrueCrime", "#Terrorism", "#CriminalMind"],
    "scandal":          ["#TrueCrime", "#Scandal", "#CriminalMind"],
    "crime":            ["#TrueCrime", "#Crime", "#Documentary"],
    "organized crime":  ["#TrueCrime", "#OrganizedCrime", "#Mafia"],
    "political crime":  ["#TrueCrime", "#PoliticalCrime", "#Documentary"],
    "war crime":        ["#TrueCrime", "#WarCrime", "#Documentary"],
    "espionage":        ["#TrueCrime", "#Espionage", "#Spy"],
    "heist":            ["#TrueCrime", "#Heist", "#CriminalMind"],
}


def _build_topic_hashtags(keyword: str, data: dict) -> str:
    """Build relevant hashtag string for a topic based on its type and name."""
    ttype = data.get("type", "crime").lower()
    base_tags = _TYPE_HASHTAGS.get(ttype, ["#TrueCrime", "#Documentary"])

    # Add name-based hashtag
    name_tag = "#" + "".join(w.capitalize() for w in keyword.split())
    tags = list(dict.fromkeys([name_tag] + base_tags + ["#DarkCrimeDecoded"]))
    return " ".join(tags[:6])


def _normalize_topic_title(title: str) -> str:
    """
    Validate and clean a topic title before display.
    Canonical slugs (jeffrey_epstein, ted-bundy) are converted to Title Case
    and always accepted — they are deterministic topic identifiers.
    Returns "" if the title is truncated, too short, or malformed.
    """
    if not title:
        return ""
    raw = title.strip()

    # Canonical slug → Title Case (always accept; no length gate needed)
    if _SLUG_PAT_PIPE.match(raw):
        converted = " ".join(w.capitalize() for w in _re_pipeline.split(r"[_\-]+", raw))
        print(f"[TOPIC] Canonical slug detected: '{raw}' → '{converted}'")
        return converted

    title = raw
    if len(title) < 15:
        return ""
    # Reject titles that end mid-word (truncated — no terminal punctuation
    # and last word is suspiciously short)
    if title[-1].isalnum():
        last_word = title.split()[-1]
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
      - topic dict  if user selects a number or sends free-text topic
      - "CANCEL"    if user sends /cancel or selects the Cancel option
      - "CANCEL"    if timeout expires (no auto-selection — pipeline aborts)

    Supported replies:
      1 / 2 / 3  — pick from candidate menu
      N          — numbered Cancel option aborts
      /cancel    — abort pipeline
      /refresh   — re-send candidate menu
      <free text> — manual topic override (validated before accepting)
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
        # No auto-candidates — still show free-text prompt so user can type manually
        print("[TOPIC] No valid candidates — sending free-text prompt to Telegram")
        cancel_n  = 1
        menu_text = (
            "[ANIMATION PIPELINE] Auto-research found no topic candidates.\n\n"
            "Type a topic directly (e.g. 'Pablo Escobar', 'Jeffrey Dahmer').\n\n"
            "Or /cancel to stop.\n"
            f"Timeout: {timeout_sec // 60} min."
        )
    else:
        # Build the numbered menu (show source label + hashtags for registry picks)
        lines = ["[ANIMATION PIPELINE]\nSelect a topic:\n"]
        for i, c in enumerate(valid, 1):
            _src    = " [registry]" if c.get("_source") == "registry" else " [fresh]"
            _show   = f"  ↳ {c['show']}" if c.get("show") else ""
            _htags  = f"\n   {c['hashtags']}" if c.get("hashtags") else ""
            lines.append(f"{i}. {c['topic']}{_src}{_show}{_htags}")
        cancel_n = len(valid) + 1
        lines.append(f"\n{cancel_n}. Cancel")
        reply_hint = " / ".join(str(i) for i in range(1, cancel_n + 1))
        lines.append(f"\nReply with: {reply_hint}")
        lines.append("Or: /cancel · /refresh")
        lines.append("Or type any topic directly to override.")
        lines.append(f"Timeout: {timeout_sec // 60} min → auto-select #1")
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

            # Cancel (command or numbered cancel option)
            if cmd in ("/cancel", "cancel", str(cancel_n)):
                print("[TOPIC] User cancelled via Telegram")
                _tg_send(base, chat_id, "[ANIMATION PIPELINE] Cancelled.")
                return "CANCEL"

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
                # Out-of-range number — ignore silently, keep waiting
                continue
            except ValueError:
                pass

            # ── Free-text manual topic override ──────────────────────────────
            # Any non-command, non-numeric text of ≥5 chars is treated as a
            # direct topic supplied by the user.  It is normalized and scored
            # before being accepted; invalid/ambiguous input gets a helpful
            # error reply and the poll continues.
            if len(text) >= 5:
                print(f"[TOPIC] Manual Telegram override received: '{text[:80]}'")

                _norm = normalize_topic_title(text)
                if not _norm:
                    _tg_send(base, chat_id,
                        f"[ANIMATION PIPELINE]\n"
                        f"Cannot validate topic:\n\"{text[:60]}\"\n\n"
                        f"Title appears truncated or too short.\n"
                        f"Please try again or select from the numbered menu.")
                    continue
                print(f"[TOPIC] Normalized manual topic: '{_norm[:80]}'")

                _ents   = extract_canonical_entities(_norm)
                _domain = classify_topic_domain_with_context(_norm, _ents)
                _conf   = semantic_confidence_score(_norm, _ents)

                if _conf < 0.4:
                    _tg_send(base, chat_id,
                        f"[ANIMATION PIPELINE]\n"
                        f"Topic rejected (too ambiguous):\n\"{_norm[:60]}\"\n\n"
                        f"Confidence: {_conf:.2f} (minimum 0.40)\n\n"
                        f"Please send a more specific topic, or select from the numbered menu.")
                    continue

                _DOMAIN_LABELS = {
                    "tv_adaptation":   "TV / film adaptation",
                    "archaeology":     "archaeology / ancient history",
                    "serial_killer":   "serial killer / true crime",
                    "organized_crime": "organized crime / mafia",
                    "fraud":           "financial crime / fraud",
                    "war_historical":  "war / historical event",
                    "biography":       "biography / true crime",
                    "default":         "true crime / documentary",
                }
                _dlabel = _DOMAIN_LABELS.get(_domain, _domain)

                print(f"[TOPIC] Semantic validation passed: "
                      f"domain={_domain}, confidence={_conf:.2f}")
                _tg_send(base, chat_id,
                    f"[ANIMATION PIPELINE]\n"
                    f"Manual topic received:\n\"{_norm}\"\n\n"
                    f"Domain: {_dlabel}\n"
                    f"Topic validated successfully.\n\n"
                    f"Starting animation generation...")
                print(f"[TOPIC] Starting generation from manual topic: '{_norm[:60]}'")
                return {"topic": _norm, "niche": _norm}

    # Timeout — auto-select first candidate if available
    print(f"[TOPIC] No Telegram reply in {timeout_sec}s")
    if valid:
        auto = valid[0]
        _tg_send(base, chat_id,
            f"[ANIMATION PIPELINE] No reply — auto-selecting:\n{auto['topic']}\n\nStarting generation...")
        print(f"[TOPIC] Auto-selected: {auto['topic'][:60]}")
        return auto
    _tg_send(base, chat_id,
        "[ANIMATION PIPELINE] ⛔ No topic selected and no candidates. Pipeline stopped.")
    return "CANCEL"


# ── Main entry point ─────────────────────────────────────────────────────────

def run_pipeline() -> None:
    global _ctrl
    t0    = time.time()
    today = datetime.date.today().isoformat()
    stats = {"generated": 0, "posted": 0, "skipped": 0, "errors": 0}

    # Initialize ALL output path/URL variables before any conditional logic
    # to prevent UnboundLocalError when early branches exit or skip assignments.
    en_long_path  = ""
    ar_long_path  = ""
    en_short_path = ""
    ar_short_path = ""
    yt_en_url     = ""
    yt_ar_url     = ""

    _ctrl = TelegramController(mode="animation")

    print(f"\n{'='*60}")
    print(f"  [ANIMATION PIPELINE] Dark Crime Decoded — {today}")
    print(f"  PIPELINE_MODE = {os.getenv('PIPELINE_MODE','animation')}")
    print(f"  Visual mode: character-centric motion documentary")
    print(f"{'='*60}\n")

    send_message(f"[ANIMATION PIPELINE] Starting — {today}\nPreparing assets & selecting topic...")
    ensure_music_assets()

    # ── STEP 1: Topic selection (Telegram-first) ──────────────────────────────
    print(f"\n{'='*50}\n  TOPIC SELECTION\n{'='*50}\n", flush=True)

    # Allow manual topic override via environment variable OR topic_inject.json
    _manual_topic = os.getenv("ANIM_TOPIC", "").strip()
    _topic_wait_sec = int(os.getenv("ANIM_TOPIC_WAIT_SEC", "300"))
    _force_rewrite_scripts = False

    # ── topic_inject.json: direct topic confirmation (created by create_topic.py) ─
    _inject_file = os.path.join(_ROOT, "topic_inject.json")
    _inject: dict = {}
    if not _manual_topic and os.path.exists(_inject_file):
        try:
            with open(_inject_file, encoding="utf-8") as _f:
                _inject = json.load(_f)
            _manual_topic = _inject.get("topic", "").strip()
            _force_rewrite_scripts = bool(_inject.get("force_rewrite", False))
            os.remove(_inject_file)  # consume once — single-shot
            print(f"[TOPIC] topic_inject.json consumed: '{_manual_topic}'")
            if _inject.get("note"):
                print(f"[TOPIC] Note: {_inject['note']}")
            if _force_rewrite_scripts:
                print("[TOPIC] Force-rewrite mode: EN + AR scripts will be regenerated")
        except Exception as _ie:
            print(f"[TOPIC] topic_inject.json read failed ({_ie}) — continuing normally")
            _inject = {}
            _manual_topic = ""

    topic: dict = {}

    if _manual_topic:
        # User pre-supplied the topic — use immediately, no Telegram wait
        _log("Research", f"Manual topic override: '{_manual_topic}'", "OK")
        _norm_manual = normalize_topic_title(_manual_topic) or _manual_topic

        # Enrich with topics.py registry context (show, type, region, arabic_name)
        _topic_show   = _inject.get("show", "")
        _topic_type   = _inject.get("type", "")
        _topic_region = _inject.get("region", "")
        _topic_ar_nm  = _inject.get("arabic_name", "")
        _user_note    = _inject.get("note", "")

        # Build a richer user_note that includes show context for the research agent
        _research_note = _user_note
        if _topic_show and not _user_note:
            _research_note = f"Associated with TV/film: {_topic_show}"
        elif _topic_show and _user_note:
            _research_note = f"{_user_note} | Show: {_topic_show}"

        _canonical = build_canonical_research_payload(_norm_manual, manual=True)
        topic = {
            "topic":          _canonical["topic"],
            "niche":          _canonical["topic"],
            "search_query":   _canonical["search_query"],
            "keywords":       _canonical["keywords"],
            "domain":         _canonical["domain"],
            "topic_hash":     _canonical["topic_hash"],
            "entities":       _canonical["entities"],
            "research":       _canonical,
            "manual_topic":   True,
            "force_rewrite":  _force_rewrite_scripts,
            "user_note":      _research_note,
            "show":           _topic_show,
            "topic_type":     _topic_type,
            "region":         _topic_region,
            "arabic_name":    _topic_ar_nm,
        }
        if _topic_show:
            _log("Research", f"Registry: show={_topic_show!r} type={_topic_type!r}", "OK")
        _ctrl.set_topic(_norm_manual)
        _ctrl.start()
    else:
        # Auto-discover candidates, let user choose via Telegram
        _ctrl.update_stage("Research", "discovering topic candidates")
        _log("Research", "Discovering topic candidates for Telegram selection")
        try:
            # ── Build candidate pool: registry best (uncovered) + fresh DDG picks ──
            # Registry best — top uncovered topics from topics.py with hashtags
            _registry_candidates: list[dict] = []
            try:
                from topics import USA_TOPICS, WORLD_TOPICS, ARABIC_TOPICS, ALIASES
                import re as _re_t
                _all_registry = {**USA_TOPICS, **WORLD_TOPICS, **ARABIC_TOPICS}

                # Load covered topics to skip already-done ones
                _covered_set: set[str] = set()
                try:
                    with open("output/covered_topics.json", encoding="utf-8") as _cf:
                        _ct_data = json.load(_cf)
                    for _ct in (_ct_data if isinstance(_ct_data, list) else _ct_data.get("covered", [])):
                        _covered_set.add(_ct.get("topic", "").lower())
                        _covered_set.add(_ct.get("series", "").lower())
                except Exception:
                    pass

                _PRIORITY_TYPES = ("serial killer", "cartel", "mafia", "fraud")
                _sorted_keys = sorted(
                    _all_registry,
                    key=lambda k: (
                        0 if _all_registry[k].get("type") in _PRIORITY_TYPES else 1,
                        list(_all_registry.keys()).index(k),
                    )
                )
                for _rk in _sorted_keys:
                    _rdata = _all_registry[_rk]
                    _rtitle = " ".join(w.capitalize() for w in _rk.split())
                    _slug = _rtitle.lower()
                    if _slug in _covered_set or any(
                        _slug in _cs or _cs in _slug for _cs in _covered_set
                    ):
                        continue  # already covered
                    _hashtags = _build_topic_hashtags(_rk, _rdata)
                    _registry_candidates.append({
                        "topic":    _rtitle,
                        "niche":    _rtitle,
                        "show":     _rdata.get("show", ""),
                        "type":     _rdata.get("type", ""),
                        "region":   _rdata.get("region", ""),
                        "hashtags": _hashtags,
                        "_source":  "registry",
                    })
                    if len(_registry_candidates) >= 2:
                        break
                print(f"[TOPIC] Registry candidates: {[c['topic'] for c in _registry_candidates]}")
            except Exception as _re:
                print(f"[TOPIC] Registry lookup failed (non-fatal): {_re}")

            # Fresh DDG picks — new topics from web research
            _ddg_candidates: list[dict] = []
            try:
                _ddg_candidates = research_topics(count=3)
            except Exception as _de:
                print(f"[TOPIC] DDG research failed (non-fatal): {_de}")

            # Merge: registry first (best picks), then fresh (new discoveries)
            _candidates = (_registry_candidates + _ddg_candidates)[:4]
            if not _candidates:
                raise RuntimeError("Both registry and DDG topic discovery returned empty")
        except Exception as e:
            _log("Research", f"Topic discovery failed ({e}) — falling back to manual selection", "WARN")
            send_message(f"[ANIM] Auto-research failed: {e}\n\nFalling back to manual topic selection.")
            _candidates = []  # let _wait_for_topic_selection show free-text prompt

        # Wait for user selection (blocking — up to _topic_wait_sec seconds)
        _selection = _wait_for_topic_selection(_candidates, timeout_sec=_topic_wait_sec)

        # CANCEL/None: message already sent by selector; just stop cleanly
        if _selection == "CANCEL" or _selection is None:
            _log("Research", "Pipeline stopped — no topic selected", "WARN")
            return

        topic = _selection

        _manual_selection = not any(
            topic is candidate or topic.get("series") == candidate.get("series")
            for candidate in _candidates
        )
        _canonical = build_canonical_research_payload(
            topic,
            topic.get("research"),
            manual=_manual_selection,
        )
        topic.update({
            "topic":        _canonical["topic"],
            "niche":        topic.get("niche") or _canonical["topic"],
            "search_query": _canonical["search_query"],
            "keywords":     _canonical["keywords"],
            "domain":       _canonical["domain"],
            "topic_hash":   _canonical["topic_hash"],
            "entities":     _canonical["entities"],
            "research":     _canonical,
            "manual_topic": _manual_selection,
        })

        _ctrl.set_topic(topic.get("topic", ""))
        _ctrl.start()

    topic_text  = topic.get("topic", "")
    topic_niche = topic.get("niche", "")
    topic["research"] = repair_research_payload(
        topic,
        topic.get("research"),
        manual=bool(topic.get("manual_topic") or _manual_topic),
    )
    topic["search_query"] = topic.get("search_query") or topic["research"].get("search_query", topic_text)
    topic["keywords"] = topic.get("keywords") or topic["research"].get("keywords", [topic_text])

    if is_fictional(topic_text, topic_niche):
        _log("Research", f"Fictional topic blocked: '{topic_text}'", "WARN")
        send_message(f"[ANIM] Fictional topic blocked: '{topic_text}'")
        return

    _log("Research", f"Topic: '{topic_text}'", "OK")

    # Hard reset: clear all identity/character/clip state from any previous run
    init_topic_lock(topic_text)

    # Initialise persistent content storage and wire into image pipeline
    _topic_content = ensure_topic_content(topic_text)
    set_active_topic_content(_topic_content)
    _log("Research", f"Content storage: {_topic_content['path']}", "OK")

    # Force-rewrite: clear character memory so portraits are re-fetched
    if topic.get("force_rewrite"):
        _chars_cache = os.path.join(_topic_content.get("path", ""), "characters", "cast.json")
        if os.path.exists(_chars_cache):
            try:
                os.remove(_chars_cache)
                _log("Research", "Force-rewrite: cleared character cast cache", "OK")
            except Exception as _cce:
                _log("Research", f"Cast cache clear failed (non-fatal): {_cce}", "WARN")

    # ── Pre-research semantic gate ────────────────────────────────────────────
    # Two independent scores are computed:
    #   entity_conf  — is this a real identifiable subject? (structural check)
    #   semantic_conf — how certain are we about domain / completeness?
    #
    # Routing:
    #   entity_conf < 0.20              → hard abort  (garbage/malformed input)
    #   entity_conf ≥ 0.20, conf < 0.40 → soft continue (entity valid, domain uncertain)
    #   conf ≥ 0.40                     → normal continue
    _entities   = extract_canonical_entities(topic_text)
    _ctx_domain = classify_topic_domain_with_context(topic_text, _entities)
    _conf       = semantic_confidence_score(topic_text, _entities)
    _ent_conf   = entity_confidence_score(topic_text, _entities)

    _conf_label = "HIGH" if _conf >= 0.7 else ("MEDIUM" if _conf >= 0.4 else "LOW")
    _log("Research",
         f"Domain: {_ctx_domain} | Confidence: {_conf:.2f} ({_conf_label}) | "
         f"Entity: {_ent_conf:.2f}")

    if _ent_conf < 0.20:
        # No identifiable entity — hard abort
        _msg = (
            f"[ANIM] Topic rejected — no valid entity detected:\n"
            f"'{topic_text[:80]}'\n\n"
            f"Entity score: {_ent_conf:.2f} (minimum 0.20)\n"
            f"Please send a real person, event, or documentary topic."
        )
        _log("Research", f"No valid entity (ent={_ent_conf:.2f}) — aborting", "ERROR")
        send_message(_msg)
        return

    if _conf < 0.4:
        # Valid entity but uncertain domain — continue safely with warning
        _log("Research",
             f"Low domain confidence ({_conf:.2f}) but entity valid "
             f"(ent={_ent_conf:.2f}) — continuing safely", "WARN")
        send_message(
            f"[ANIM] Low domain confidence ({_conf:.2f}) for:\n"
            f"'{topic_text[:80]}'\n\n"
            f"Entity detected ({_ent_conf:.2f}). Proceeding — domain will "
            f"be refined during research."
        )
        print("[PIPELINE] Soft-fallback mode active — domain refinement deferred to research")

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
        research = build_canonical_research_payload(
            topic,
            research,
            manual=bool(topic.get("manual_topic") or _manual_topic),
            series_name=topic.get("series"),
            user_note=topic.get("user_note"),
        )
        research["real_person"] = topic_text
        topic["research"]       = research
        topic["search_query"]   = research.get("search_query", topic_text)
        topic["keywords"]       = research.get("keywords", [topic_text])
        topic["domain"]         = research.get("domain", topic.get("domain", "default"))
        topic["topic_hash"]     = research.get("topic_hash", topic.get("topic_hash", ""))
        topic["entities"]       = research.get("entities", topic.get("entities", {}))
        _log("Research", "Deep research done", "OK")
    except Exception as e:
        _log("Research", f"research_series failed (non-fatal): {e}", "WARN")
        topic["research"] = repair_research_payload(
            topic,
            topic.get("research"),
            manual=bool(topic.get("manual_topic") or _manual_topic),
        )

    # ── STEP 2: Scripts (EN + AR) ─────────────────────────────────────────────
    print(f"\n{'='*50}\n  SCRIPTS\n{'='*50}\n", flush=True)
    _ctrl.update_stage("Scripts", "writing English script")
    _log("Scripts", "Writing English script (cinematic animation style)")
    try:
        en_long = write_animation_script(topic)
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

    _meta_dir = _topic_content.get("metadata_path", "output")
    os.makedirs(_meta_dir, exist_ok=True)
    _script_txt_path = os.path.join(_meta_dir, f"anim_script_{today}.txt")
    try:
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
        # ── Expand AR script until target (ANIMATION AR: 6500-10000w / 35-60min) ──
        _AR_EXPAND_TARGET = 10_000
        if ar_wc < _AR_EXPAND_TARGET:
            _log("Scripts", f"AR {ar_wc}w below {_AR_EXPAND_TARGET}w target — expanding", "WARN")
            send_message(f"[ANIM] AR script {ar_wc}w (~{round(ar_wc/100,1)}min) — expanding to {_AR_EXPAND_TARGET}w...")
            try:
                _ar_expanded = _expand_arabic_script_to_min(ar_long["script"], target_min=_AR_EXPAND_TARGET)
                _ar_expanded_wc = len(_ar_expanded.split())
                if _ar_expanded_wc > ar_wc:
                    ar_long["script"] = _ar_expanded
                    ar_wc = _ar_expanded_wc
                    ar_long["chapters"] = generate_chapters(ar_wc, language="arabic", angle_title=en_long.get("angle_title", ""))
                    _log("Scripts", f"AR expanded: {ar_wc}w (~{round(ar_wc/100,1)}min)", "OK")
                else:
                    _log("Scripts", f"AR expansion no improvement ({_ar_expanded_wc}w) — proceeding with {ar_wc}w", "WARN")
            except Exception as _exp_e:
                _log("Scripts", f"AR expansion failed (non-fatal): {_exp_e}", "WARN")
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

    # ── Persist scripts and research into content/<topic>/metadata/ ───────────
    try:
        save_topic_metadata(
            topic_text,
            en_script=(
                f"TITLE: {en_long.get('title','')}\n"
                f"WORDS: {_en_wc}  EST: ~{_est_min} min\n"
                f"{'='*60}\n\n"
                f"{en_long.get('script','')}"
            ),
            ar_script=(
                f"TITLE: {ar_long.get('title','')}\n"
                f"{'='*60}\n\n"
                f"{ar_long.get('script','')}"
            ),
            research=topic.get("research", {}),
            metadata={
                "date":      today,
                "topic":     topic_text,
                "en_title":  en_long.get("title", ""),
                "ar_title":  ar_long.get("title", ""),
                "en_words":  _en_wc,
                "pipeline":  "animation",
            },
        )
        _log("Scripts", f"Scripts/research persisted to content/{_topic_content['topic']}/metadata/", "OK")
    except Exception as _me:
        _log("Scripts", f"Metadata persist (non-fatal): {_me}", "WARN")

    # ── Approval gate 1: Scripts ─────────────────────────────────────────────
    _ar_wc_display   = len(ar_long.get("script", "").split())
    _ar_est_min_disp = round(_ar_wc_display / 100, 1)
    _ar_status       = (
        f"⚠️ SHORT (~{_ar_est_min_disp}min, target 10000w/60min)"
        if _ar_wc_display < 10_000
        else f"~{_ar_est_min_disp} min"
    )
    while True:
        _approval_1 = wait_for_approval(
            stage_name=(
                f"Scripts Ready — {en_long.get('title', topic_text)[:60]}\n"
                f"EN: {_en_wc} words (~{_est_min} min)\n"
                f"AR: {_ar_wc_display} words ({_ar_status})"
            ),
            available_commands=["approve", "rewrite", "cancel"],
            mode="ANIMATION",
        )
        if _approval_1 == "cancel":
            send_message("[ANIM] Pipeline cancelled at scripts gate.")
            _ctrl.stop()
            return
        elif _approval_1 == "approve":
            break
        elif _approval_1 == "rewrite":
            _log("Scripts", "Rewrite requested — regenerating all scripts", "WARN")
            send_message("[ANIM] Rewriting scripts...")
            try:
                en_long  = write_animation_script(topic)
                _en_wc   = clean_word_count(en_long.get("script", ""))
                _est_min = round(_en_wc / WORDS_PER_MINUTE, 1)
                _ctrl.set_latest_script(en_long)
                send_english_script_preview(en_long, label=f"[ANIM] REWRITTEN — {en_long.get('title','')}")
                ar_long  = translate_script(en_long, research=topic.get("research", {}))
                send_arabic_script_preview(ar_long)
            except Exception as _re:
                send_message(f"[ANIM] Rewrite failed: {_re}")

    # ── STEP 3: Generate animation videos (EN + AR) ───────────────────────────
    print(f"\n{'='*50}\n  ANIMATION VIDEO GENERATION\n{'='*50}\n", flush=True)
    _log("AnimGen", "Starting character-centric motion documentary generation")
    _log("AnimGen", f"Visual style: {topic_text} — real photo + D-ID portrait + motion clips")

    os.makedirs(FINAL_DIR, exist_ok=True)

    _ctrl.update_stage("AnimGen", "generating AR animation video")
    _log("AnimGen", "Generating AR animation video")
    _ar_wc_initial = len(ar_long.get("script", "").split())
    if _ar_wc_initial < 50:
        _log("AnimGen", f"[AR SKIP] Script empty ({_ar_wc_initial}w) — skipping Arabic render", "ERROR")
        ar_long_path = ""
    else:
        if _ar_wc_initial < 8_000:
            _log("AnimGen", f"[AR SHORT] {_ar_wc_initial}w (~{round(_ar_wc_initial/175,1)}min) below 8000w target — rendering anyway", "WARN")
        ar_long_path = _make_animation_video(ar_long, topic.get("research", {}), FINAL_DIR, stats, "AR long")

    # ── Arabic runtime auto-rebuild ───────────────────────────────────────────
    # Validate ACTUAL rendered video duration via ffprobe. Tiers:
    #   <30m = FAIL  |  30-35m = UNDER TARGET → expand  |  35-60m = IDEAL → export  |  >60m = export
    _AR_IDEAL_SECS = 2100  # 35 min — ANIMATION AR export floor
    _AR_HARD_FAIL  = 1800  # 30 min — universal absolute minimum
    _ar_rebuild    = 0
    _ar_max_rb     = 4
    _ar_is_timeout_fallback = bool(ar_long_path and "_fb." in os.path.basename(ar_long_path))
    while ar_long_path and os.path.exists(ar_long_path):
        _ar_secs = _video_secs(ar_long_path)
        _ar_mins = _ar_secs / 60
        if _ar_secs < _AR_HARD_FAIL:
            _ar_status = "FAIL"
        elif _ar_secs < _AR_IDEAL_SECS:
            _ar_status = "UNDER TARGET"
        elif _ar_secs <= 3600:
            _ar_status = "IDEAL"
        else:
            _ar_status = "ACCEPTABLE LONGFORM"
        _log("AnimGen", f"[AR RUNTIME] Target: 35-60m | Rendered: {_ar_mins:.1f}m | Status: {_ar_status}")
        if _ar_status in ("IDEAL", "ACCEPTABLE LONGFORM"):
            _log("AnimGen", f"[AR PASSED] {_ar_mins:.1f}min — {_ar_status}", "OK")
            break
        if _ar_is_timeout_fallback:
            _ar_est = round(len(ar_long.get("script", "").split()) / 100, 1)
            _log("AnimGen", f"[AR TIMEOUT] Render timed out — script ~{_ar_est}min estimated. Increase render timeout.", "ERROR")
            send_message(f"[ANIM] AR render timed out — script is {_ar_est}min, CI render needs >120min. Fallback clip retained.")
            break
        _ar_rebuild += 1
        if _ar_rebuild > _ar_max_rb:
            _log("AnimGen", f"[AR EXPANSION] Rebuild limit reached — continuing with {_ar_mins:.1f}min video", "WARN")
            send_message(f"[ANIM] Arabic runtime {_ar_mins:.1f}min after {_ar_max_rb} rebuilds — proceeding")
            break
        _log("AnimGen", f"[AR EXPANSION] {_ar_status}: {_ar_mins:.1f}min — fallback expansion ({_ar_rebuild}/{_ar_max_rb})", "WARN")
        send_message(f"[ANIM] Arabic {_ar_status}: {_ar_mins:.1f}min | target 35min — fallback expansion ({_ar_rebuild}/{_ar_max_rb})...")
        from agent.script_agent import expand_arabic_runtime as _ear
        _ar_wc_pre = len(ar_long["script"].split())
        ar_long["script"] = _ear(ar_long["script"], target_min=47.5, topic=topic_text)
        _ar_wc_post = len(ar_long["script"].split())
        if _ar_wc_post <= _ar_wc_pre:
            _log("AnimGen", f"[AR EXPANSION] Expansion added 0 words (content refused or rate-limited) — accepting {_ar_mins:.1f}min video", "WARN")
            send_message(f"[ANIM] AR expansion failed (0 words added) — accepting {_ar_mins:.1f}min video and continuing")
            break
        ar_long_path = _make_animation_video(ar_long, topic.get("research", {}), FINAL_DIR, stats, "AR long")
        _ar_is_timeout_fallback = bool(ar_long_path and "_fb." in os.path.basename(ar_long_path))

    _check_cancel("after AR animation render")

    _ctrl.update_stage("AnimGen", "generating EN animation video")
    _log("AnimGen", "Generating EN animation video (character-identity locked)")
    en_long_path = _make_animation_video(en_long, topic.get("research", {}), FINAL_DIR, stats, "EN long")

    # ── English runtime validation (ANIMATION EN target: 12-18 min) ──────────
    _EN_MAX_SECS = 1080  # 18 minutes — English animation max
    _EN_MIN_SECS = 720   # 12 minutes — English animation min
    _en_comp     = 0
    _en_max_comp = 2
    while en_long_path and os.path.exists(en_long_path):
        _en_secs   = _video_secs(en_long_path)
        _en_mins   = _en_secs / 60
        _en_status = "PASS" if _EN_MIN_SECS <= _en_secs <= _EN_MAX_SECS else ("OVER LIMIT" if _en_secs > _EN_MAX_SECS else "UNDER")
        _log("AnimGen", f"[EN RUNTIME] Target: 12-18m | Rendered: {_en_mins:.1f}m | Status: {_en_status}")
        if _en_secs <= _EN_MAX_SECS:
            if _en_secs < _EN_MIN_SECS:
                _log("AnimGen", f"[EN WARN] {_en_mins:.1f}min < 12min — content may be too sparse", "WARN")
            break
        _en_comp += 1
        if _en_comp > _en_max_comp:
            _log("AnimGen", f"[EN RUNTIME] Over limit after {_en_max_comp} compressions — proceeding with {_en_mins:.1f}min", "WARN")
            break
        _target_wc = int(len(en_long.get("script", "").split()) * (_EN_MAX_SECS / _en_secs) * 0.92)
        _log("AnimGen", f"[EN COMPRESSION] {_en_mins:.1f}min > 18min — compressing to ~{_target_wc}w (attempt {_en_comp}/{_en_max_comp})", "WARN")
        send_message(f"[ANIM] English runtime {_en_mins:.1f}min > 18min — compressing (attempt {_en_comp}/{_en_max_comp})...")
        from agent.script_agent import compress_english_script as _ces
        _compressed_en = _ces(en_long.get("script", ""), target_words=_target_wc, topic=topic_text)
        if len(_compressed_en.split()) < len(en_long.get("script", "").split()) * 0.97:
            en_long["script"] = _compressed_en
            en_long_path = _make_animation_video(en_long, topic.get("research", {}), FINAL_DIR, stats, "EN long") or en_long_path
        else:
            _log("AnimGen", "[EN COMPRESSION] No meaningful reduction — stopping compression", "WARN")
            break

    _check_cancel("after EN animation render")

    # ── STEP 4: Promo shorts (cut from long animation videos) ─────────────────
    # Animation pipeline does not re-render a separate short script —
    # the motion clips are already the best possible visuals. We cut the
    # strongest moment from the finished long animation video instead.
    print(f"\n{'='*50}\n  SHORTS\n{'='*50}\n", flush=True)
    _ctrl.update_stage("Shorts", "cutting EN promo short from animation")

    en_short_path = ""
    ar_short_path = ""
    _dedicated_short = {}
    try:
        _dedicated_short = write_short_script(en_long)
    except Exception as _se:
        _log("Shorts", f"Dedicated short script failed: {_se}", "WARN")

    if _dedicated_short:
        ar_short_path = _make_standalone_short(
            _dedicated_short, topic.get("research", {}), FINAL_DIR, stats, "AR standalone short", "arabic"
        )
        en_short_path = _make_standalone_short(
            _dedicated_short, topic.get("research", {}), FINAL_DIR, stats, "EN standalone short", "english"
        )

    if (not en_long_path or not os.path.exists(en_long_path)) and (not ar_long_path or not os.path.exists(ar_long_path)):
        _log("Shorts", "[SHORT] Long render unavailable", "WARN")
        _log("Shorts", "[SHORT] Continuing standalone generation", "WARN")

    _ctrl.update_stage("Shorts", "cutting AR promo short from animation")
    if not ar_short_path and ar_long_path and os.path.exists(ar_long_path):
        _log("Shorts", "Cutting AR short from animation video")
        try:
            _cuts = cut_best_short(ar_long_path, ar_long)
            ar_short_path = _cuts[0]["path"] if _cuts else ""
            if ar_short_path:
                _ar_s_secs = _video_secs(ar_short_path)
                print(f"[SHORT RUNTIME] AR short (cut): {_ar_s_secs:.1f}s")
                if _ar_s_secs < 60:
                    _log("Shorts", f"[SHORT BLOCKED] AR cut short {_ar_s_secs:.1f}s < 60s — will not upload", "ERROR")
                    ar_short_path = ""
                else:
                    _log("Shorts", f"[SHORT PASSED] AR short: {_ar_s_secs:.1f}s — {os.path.basename(ar_short_path)}", "OK")
            else:
                _log("Shorts", "AR short cut returned no clip", "WARN")
        except Exception as _ce:
            _log("Shorts", f"AR short cut failed: {_ce}", "ERROR")
    else:
        _log("Shorts", "AR long video missing — cannot cut short", "WARN")

    _ctrl.update_stage("Shorts", "cutting EN promo short from animation")
    if not en_short_path and en_long_path and os.path.exists(en_long_path):
        _log("Shorts", "Cutting EN short from animation video")
        try:
            _cuts = cut_best_short(en_long_path, en_long)
            en_short_path = _cuts[0]["path"] if _cuts else ""
            if en_short_path:
                _en_s_secs = _video_secs(en_short_path)
                print(f"[SHORT RUNTIME] EN short (cut): {_en_s_secs:.1f}s")
                if _en_s_secs < 60:
                    _log("Shorts", f"[SHORT BLOCKED] EN cut short {_en_s_secs:.1f}s < 60s — will not upload", "ERROR")
                    en_short_path = ""
                else:
                    _log("Shorts", f"[SHORT PASSED] EN short: {_en_s_secs:.1f}s — {os.path.basename(en_short_path)}", "OK")
            else:
                _log("Shorts", "EN short cut returned no clip", "WARN")
        except Exception as _ce:
            _log("Shorts", f"EN short cut failed: {_ce}", "ERROR")
    else:
        _log("Shorts", "EN long video missing — cannot cut short", "WARN")

    # ── Approval gate 2: Render complete ─────────────────────────────────────
    while True:
        _approval_2 = wait_for_approval(
            stage_name="Render Complete — Ready to Upload",
            available_commands=["approve", "publish", "rerender", "cancel"],
            mode="ANIMATION",
        )
        if _approval_2 in ("approve", "publish"):
            break
        elif _approval_2 == "cancel":
            send_message("[ANIM] Pipeline cancelled at render gate.")
            _ctrl.stop()
            return
        elif _approval_2 == "rerender":
            _log("AnimGen", "Re-render requested — regenerating animation videos", "WARN")
            send_message("[ANIM] Re-rendering animation videos...")
            en_long_path = _make_animation_video(
                en_long, topic.get("research", {}), FINAL_DIR, stats, "EN long"
            )
            _ar_wc_recheck = len(ar_long.get("script", "").split())
            if _ar_wc_recheck >= 50:
                ar_long_path = _make_animation_video(
                    ar_long, topic.get("research", {}), FINAL_DIR, stats, "AR long"
                )
            else:
                ar_long_path = ""
            en_short_path = ""
            ar_short_path = ""
            _rerender_short = {}
            try:
                _rerender_short = write_short_script(en_long)
            except Exception:
                _rerender_short = {}
            if _rerender_short:
                en_short_path = _make_standalone_short(
                    _rerender_short, topic.get("research", {}), FINAL_DIR, stats, "EN standalone short", "english"
                )
                ar_short_path = _make_standalone_short(
                    _rerender_short, topic.get("research", {}), FINAL_DIR, stats, "AR standalone short", "arabic"
                )
            if not en_short_path and en_long_path and os.path.exists(en_long_path):
                try:
                    _cuts = cut_best_short(en_long_path, en_long)
                    _p = _cuts[0]["path"] if _cuts else ""
                    if _p and _video_secs(_p) >= 60:
                        en_short_path = _p
                    elif _p:
                        _log("Shorts", f"[SHORT BLOCKED] EN rerender cut {_video_secs(_p):.1f}s < 60s", "ERROR")
                except Exception:
                    pass
            if not ar_short_path and ar_long_path and os.path.exists(ar_long_path):
                try:
                    _cuts = cut_best_short(ar_long_path, ar_long)
                    _p = _cuts[0]["path"] if _cuts else ""
                    if _p and _video_secs(_p) >= 60:
                        ar_short_path = _p
                    elif _p:
                        _log("Shorts", f"[SHORT BLOCKED] AR rerender cut {_video_secs(_p):.1f}s < 60s", "ERROR")
                except Exception:
                    pass

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
