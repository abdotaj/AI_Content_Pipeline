# ============================================================
#  agents/content_agent.py  —  Ingest user-provided content files
#
#  Checks content/ and content/pending/ for .txt and .docx files.
#  Extracts text, passes to script_agent to format into video scripts,
#  then moves processed files to content/processed/.
# ============================================================
from pathlib import Path


CONTENT_DIR   = Path("content")
PENDING_DIR   = CONTENT_DIR / "pending"
PROCESSED_DIR = CONTENT_DIR / "processed"


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


def _collect_files() -> list[Path]:
    """Return all unprocessed .txt/.docx files from content/ and content/pending/."""
    files = []
    for folder in (CONTENT_DIR, PENDING_DIR):
        if folder.exists():
            files += sorted(folder.glob("*.txt")) + sorted(folder.glob("*.docx"))
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


def ingest_content_files() -> list[dict]:
    """
    Check content/ and content/pending/ for .txt/.docx files.
    For each file: extract text → build topic → write Arabic + English scripts.
    Move processed files to content/processed/.
    Returns a flat list of script_data dicts (2 per file: arabic + english).
    """
    from agents.script_agent import write_script

    files = _collect_files()
    if not files:
        return []

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    scripts = []

    for fp in files:
        print(f"[Content] Processing: {fp.name}")
        raw_text = _extract_text(fp)
        if not raw_text:
            print(f"[Content] Empty or unreadable, skipping: {fp.name}")
            fp.rename(PROCESSED_DIR / fp.name)
            continue

        topic = _build_topic(raw_text, fp)
        print(f"[Content] Topic: {topic['topic'][:80]}")

        for lang in ("arabic", "english"):
            try:
                script_data = write_script(topic, language=lang)
                scripts.append(script_data)
            except Exception as e:
                print(f"[Content] Script generation failed ({lang}): {e}")

        fp.rename(PROCESSED_DIR / fp.name)
        print(f"[Content] Moved to processed/: {fp.name}")

    return scripts
