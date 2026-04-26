# ============================================================
#  agents/content_agent.py  —  Ingest user-provided content files
#
#  Checks content/ and content/pending/ for .txt and .docx files.
#  Extracts text, passes to script_agent to format into video scripts,
#  then moves processed files to content/processed/.
# ============================================================
from pathlib import Path


_DEFAULT_CONTENT_DIR = Path("content")


def _extract_text(file_path: Path) -> str:
    """Extract plain text from a .txt or .docx file."""
    if file_path.suffix.lower() == ".docx":
        try:
            import docx
            doc = docx.Document(str(file_path))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            print("[Content] python-docx not installed — run: pip install python-docx")
            return ""
    return file_path.read_text(encoding="utf-8").strip()


def _collect_files(content_dir: Path) -> list[Path]:
    """Return all unprocessed .txt/.docx/.json files from content_dir and content_dir/pending/."""
    files = []
    for folder in (content_dir, content_dir / "pending"):
        if folder.exists():
            files += sorted(folder.glob("*.txt")) + sorted(folder.glob("*.docx")) + sorted(folder.glob("*.json"))
    return files


def _build_topic(raw_text: str, file_path: Path) -> dict:
    """Build a topic dict from raw file text for script_agent."""
    first_line = raw_text.splitlines()[0].strip() if raw_text else file_path.stem
    words      = first_line.split()
    return {
        "topic":        first_line[:200],
        "niche":        "AI & Tech news",
        "angle":        "",
        "keywords":     words[:3] if words else ["technology"],
        "search_query": " ".join(words[:3]) if words else "technology",
    }


def _do_quick_research(topic_text: str) -> tuple[dict, bool]:
    """
    Wikipedia + DuckDuckGo research for a topic.
    Returns (research_dict, found_sources).
    found_sources is True when combined results exceed 200 words.
    """
    try:
        from agents.research_agent import fetch_wikipedia, web_search
    except ImportError:
        try:
            from agent.research_agent import fetch_wikipedia, web_search
        except ImportError:
            from research_agent import fetch_wikipedia, web_search

    wiki = ""
    ddg  = ""

    try:
        wiki = fetch_wikipedia(topic_text) or ""
    except Exception as e:
        print(f"[Content] Wikipedia lookup failed: {e}")

    try:
        ddg = web_search(f"{topic_text} real story true crime documentary", max_results=3)
    except Exception as e:
        print(f"[Content] DuckDuckGo lookup failed: {e}")

    combined   = (wiki + " " + ddg).strip()
    word_count = len(combined.split())
    print(f"[Content] Research for '{topic_text}': {word_count} words found")

    if word_count < 200:
        return {}, False

    research = {
        "series":                        topic_text,
        "real_story":                    wiki,
        "research_facts":                [s.strip() for s in ddg.split(". ") if len(s.strip()) > 30][:5],
        "research_inaccuracies":         [],
        "research_shocking":             [],
        "what_show_got_right":           [],
        "what_show_got_wrong":           [],
        "shocking_real_facts":           [],
        "real_people_behind_characters": {},
    }
    return research, True


def _send_telegram(text: str) -> None:
    """Best-effort Telegram message — never raises."""
    try:
        from agents.notify_agent import send_message
    except ImportError:
        try:
            from agent.notify_agent import send_message
        except ImportError:
            try:
                from notify_agent import send_message
            except ImportError:
                print(f"[Content] (Telegram not available) {text}")
                return
    try:
        send_message(text)
    except Exception as e:
        print(f"[Content] Telegram send failed: {e}")


def listen_for_content(timeout: int = 60) -> list[dict]:
    """
    Poll Telegram for topic messages, do quick Wikipedia + DuckDuckGo research,
    send a confirmation message, and return a list of enriched topic dicts.

    Returns an empty list if no messages arrive within `timeout` seconds.
    Script generation always proceeds regardless of whether sources were found.
    """
    import time
    import requests

    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    except ImportError:
        print("[Content] Config not available — skipping Telegram poll")
        return []

    _SKIP_WORDS = [
        "approve", "reject", "skip", "pipeline starting", "daily report",
        "generated", "posted", "errors", "script sent", "video ready",
        "youtube", "http", "telegram", "upload failed", "dark crime decoded",
    ]

    base_url    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    topics: list[dict] = []
    offset      = None
    elapsed     = 0
    poll_interval = 5

    print(f"[Content] Polling Telegram for topics ({timeout}s)...")

    while elapsed < timeout:
        params = {"timeout": poll_interval, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset

        try:
            r = requests.get(f"{base_url}/getUpdates", params=params, timeout=poll_interval + 5)
            updates = r.json().get("result", [])
        except Exception:
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue

        for update in updates:
            offset  = update["update_id"] + 1
            msg     = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = msg.get("text", "").strip()

            if not text or text.startswith("/"):
                continue
            if str(chat_id) != str(TELEGRAM_CHAT_ID):
                continue
            if any(w in text.lower() for w in _SKIP_WORDS):
                print(f"[Content] Skipping system message: {text[:60]!r}")
                continue
            if not (2 < len(text) < 300):
                continue

            topic_text = text.strip()
            print(f"[Content] Topic received: {topic_text!r}")

            # Research — never blocks progress
            research, found_sources = _do_quick_research(topic_text)

            if found_sources:
                print(f"[Content] Sources found for '{topic_text}'")
                _send_telegram(f"Found real sources for {topic_text} — creating video")
            else:
                print(f"[Content] No source found — generating original story for '{topic_text}'")
                _send_telegram(f"No sources found — writing original story for {topic_text}")

            from agents.script_agent import get_series_for_person as _gsfp
            series_info = _gsfp(topic_text)
            series_name = series_info[0] if series_info else None

            topics.append({
                "topic":        topic_text,
                "niche":        f"Real story — {series_name or topic_text}",
                "angle":        "",
                "keywords":     topic_text.split()[:3],
                "search_query": topic_text,
                "series_name":  series_name,
                "research":     research,
            })

        elapsed += poll_interval

    if topics:
        print(f"[Content] Received {len(topics)} topic(s) from Telegram")
    else:
        print("[Content] No topics received from Telegram")

    return topics


def ingest_content_files(content_dir: str | None = None) -> list[dict]:
    """
    Check content_dir (default: content/) and content_dir/pending/ for .txt/.docx/.json files.
    - .json files: loaded directly as script_data dicts (scripts already written).
    - .txt/.docx files: extract text → if short topic name (<= 50 words) do quick research
      before writing script; longer content is used verbatim as script body.
    Move processed files to content_dir/processed/.
    Returns a flat list of script_data dicts.
    """
    import json
    from agents.script_agent import write_script

    base_dir      = Path(content_dir) if content_dir else _DEFAULT_CONTENT_DIR
    processed_dir = base_dir / "processed"

    files = _collect_files(base_dir)
    if not files:
        return []

    processed_dir.mkdir(parents=True, exist_ok=True)
    scripts = []

    for fp in files:
        print(f"[Content] Processing: {fp.name}")

        # JSON files contain ready-made script_data dicts — load directly
        if fp.suffix.lower() == ".json":
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("script") and item.get("title"):
                        scripts.append(item)
                        print(f"[Content] Loaded script: {item['title'][:60]}")
                    else:
                        print(f"[Content] Skipping invalid entry in {fp.name}")
            except Exception as e:
                print(f"[Content] Failed to parse {fp.name}: {e}")
            fp.replace(processed_dir / fp.name)
            print(f"[Content] Moved to processed/: {fp.name}")
            continue

        raw_text = _extract_text(fp)
        if not raw_text:
            print(f"[Content] Empty or unreadable, skipping: {fp.name}")
            fp.replace(processed_dir / fp.name)
            continue

        topic = _build_topic(raw_text, fp)
        topic_text = topic["topic"]
        word_count = len(raw_text.split())
        print(f"[Content] Topic: {topic_text[:80]} ({word_count} words)")

        # Short file = topic name only — do research before scripting
        if word_count <= 50:
            research, found_sources = _do_quick_research(topic_text)
            if found_sources:
                print(f"[Content] Sources found for '{topic_text}' — using as research context")
                _send_telegram(f"Found real sources for {topic_text} — creating video")
                topic["research"] = research
            else:
                print(f"[Content] No source found — generating original story for '{topic_text}'")
                _send_telegram(f"No sources found — writing original story for {topic_text}")
                topic["research"] = {}

        for lang in ("arabic", "english"):
            try:
                script_data = write_script(topic, language=lang)
                scripts.append(script_data)
            except Exception as e:
                print(f"[Content] Script generation failed ({lang}): {e}")

        fp.replace(processed_dir / fp.name)
        print(f"[Content] Moved to processed/: {fp.name}")

    return scripts
