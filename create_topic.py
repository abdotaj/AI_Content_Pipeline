#!/usr/bin/env python3
# create_topic.py
# ============================================================
#  TOPIC INJECTION TOOL
#
#  Creates a topic_inject.json that animation_pipeline picks up
#  on next startup — bypasses Telegram wait, force-rewrites
#  both English and Arabic scripts.
#
#  Usage:
#    python create_topic.py ted_bundy
#    python create_topic.py "Jeffrey Epstein"
#    python create_topic.py pablo_escobar --note "focus on Medellin cartel"
#    python create_topic.py ted_bundy --no-rewrite    (keep cached scripts)
#    python create_topic.py --list                    (show all known topics)
#    python create_topic.py --show                    (show pending inject)
#    python create_topic.py --cancel                  (delete pending inject)
#
#  Then run:
#    python run_animation.py       (or trigger GitHub Actions)
#
#  The pipeline will:
#    1. Detect topic_inject.json on startup
#    2. Use the topic directly (no Telegram wait, no topic discovery)
#    3. Force-rewrite EN + AR scripts fresh
#    4. Delete topic_inject.json after consuming it (single-shot)
# ============================================================

import os
import sys
import re
import json
import argparse
import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

INJECT_FILE = os.path.join(_ROOT, "topic_inject.json")

_SLUG_PATTERN = re.compile(r'^[a-z][a-z0-9]*(?:[_\-][a-z0-9]+)+$')


def _slug_to_title(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[_\-]+", slug))


def _canonical_id(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"['''']", "", slug)
    slug = re.sub(r"[^a-z0-9\s]", " ", slug)
    slug = re.sub(r"\s+", "_", slug.strip())
    return slug[:60]


def _load_topics_registry() -> tuple[dict, dict]:
    """
    Load combined topic registry from topics.py.
    Returns (combined_topics, aliases).
    """
    try:
        from topics import USA_TOPICS, WORLD_TOPICS, ARABIC_TOPICS, ALIASES, normalize_topic
        combined = {**USA_TOPICS, **WORLD_TOPICS, **ARABIC_TOPICS}
        return combined, ALIASES
    except ImportError:
        return {}, {}


def lookup_topic(raw: str) -> dict | None:
    """
    Look up a topic in the registry by slug, alias, or display name.
    Returns the topic data dict or None.
    """
    combined, aliases = _load_topics_registry()
    if not combined:
        return None

    key = raw.lower().strip()
    # Slug → key (convert underscores to spaces)
    if _SLUG_PATTERN.match(key):
        key = key.replace("_", " ").replace("-", " ")

    # Direct match
    if key in combined:
        return {"keyword": key, **combined[key]}

    # Alias resolution
    canonical = aliases.get(key)
    if canonical and canonical in combined:
        return {"keyword": canonical, **combined[canonical]}

    # Fuzzy: check if any key starts with the input
    for ckey, cdata in combined.items():
        if ckey.startswith(key) or key.startswith(ckey):
            return {"keyword": ckey, **cdata}

    return None


def resolve_topic(raw: str) -> tuple[str, str]:
    """
    Return (canonical_id, display_title).
    Checks topics.py registry first, then normalizes from input.
    """
    raw = raw.strip()

    # Try registry lookup
    entry = lookup_topic(raw)
    if entry:
        title = " ".join(w.capitalize() for w in entry["keyword"].split())
        cid   = _canonical_id(entry["keyword"])
        return cid, title

    # Slug → Title Case
    if _SLUG_PATTERN.match(raw.lower()):
        title = _slug_to_title(raw.lower())
        cid   = raw.lower().replace("-", "_")
        return cid, title

    # Natural language input
    return _canonical_id(raw), raw


def build_inject(
    raw_topic: str,
    force_rewrite: bool = True,
    note: str = "",
) -> dict:
    cid, title = resolve_topic(raw_topic)

    # Enrich with registry metadata if available
    entry = lookup_topic(raw_topic)
    show   = entry.get("show", "") if entry else ""
    region = entry.get("region", "") if entry else ""
    ttype  = entry.get("type", "") if entry else ""
    arabic = entry.get("arabic", "") if entry else ""

    inject = {
        "canonical_id":  cid,
        "topic":         title,
        "niche":         title,
        "show":          show,
        "type":          ttype,
        "region":        region,
        "arabic_name":   arabic,
        "force_rewrite": force_rewrite,
        "force_en":      force_rewrite,
        "force_ar":      force_rewrite,
        "note":          note or "",
        "created_at":    datetime.datetime.now().isoformat(timespec="seconds"),
    }

    with open(INJECT_FILE, "w", encoding="utf-8") as f:
        json.dump(inject, f, indent=2, ensure_ascii=False)

    return inject


def list_topics() -> None:
    combined, _ = _load_topics_registry()
    if not combined:
        print("topics.py not found or empty.")
        return
    print(f"\nAvailable topics ({len(combined)} total):\n")
    for key, data in combined.items():
        slug = _canonical_id(key)
        show = data.get("show", "")
        region = data.get("region", "")
        label = f"  {slug:<40} {show}"
        if region:
            label += f"  [{region}]"
        print(label)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a topic injection file for the animation pipeline."
    )
    parser.add_argument(
        "topic",
        nargs="?",
        help='Topic slug or display name. e.g. ted_bundy or "Ted Bundy"',
    )
    parser.add_argument(
        "--no-rewrite",
        action="store_true",
        help="Do NOT force script rewrite — reuse cached scripts if available.",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Optional context note passed to the research agent.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all topics in the registry and exit.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show current topic_inject.json and exit.",
    )
    parser.add_argument(
        "--cancel",
        action="store_true",
        help="Delete current topic_inject.json and exit.",
    )
    args = parser.parse_args()

    if args.list:
        list_topics()
        return

    if args.show:
        if os.path.exists(INJECT_FILE):
            with open(INJECT_FILE, encoding="utf-8") as f:
                print(json.dumps(json.load(f), indent=2, ensure_ascii=False))
        else:
            print("No topic_inject.json found.")
        return

    if args.cancel:
        if os.path.exists(INJECT_FILE):
            os.remove(INJECT_FILE)
            print("Deleted topic_inject.json.")
        else:
            print("Nothing to cancel — topic_inject.json does not exist.")
        return

    if not args.topic:
        parser.print_help()
        sys.exit(1)

    force  = not args.no_rewrite
    inject = build_inject(args.topic, force_rewrite=force, note=args.note)

    print(f"\n[TOPIC] topic_inject.json created")
    print(f"  canonical_id  : {inject['canonical_id']}")
    print(f"  topic         : {inject['topic']}")
    if inject.get("show"):
        print(f"  show          : {inject['show']}")
    if inject.get("type"):
        print(f"  type          : {inject['type']}")
    if inject.get("region"):
        print(f"  region        : {inject['region']}")
    if inject.get("arabic_name"):
        print(f"  arabic_name   : {inject['arabic_name']}")
    print(f"  force_rewrite : {inject['force_rewrite']}")
    if inject["note"]:
        print(f"  note          : {inject['note']}")
    print(f"\nNext step — run the animation pipeline:")
    print(f"  python run_animation.py")
    print(f"  -- or trigger GitHub Actions animation workflow --\n")


if __name__ == "__main__":
    main()
