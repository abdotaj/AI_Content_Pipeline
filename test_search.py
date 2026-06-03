"""
test_search.py — Test image/video search for a given topic.

Usage:
    python test_search.py                          # default: Ted Bundy
    python test_search.py "Pablo Escobar"
    python test_search.py "Pablo Escobar" --chunk "arrested Medellin 1993"
    python test_search.py "Ted Bundy" --arabic     # test Arabic translation
    python test_search.py "Narcos" --video         # also test video search
"""

import sys
import os
import time
import argparse

# Load .env so API keys (PEXELS_API_KEY, PIXABAY_API_KEY) are available
from dotenv import load_dotenv
load_dotenv()

# Silence MoviePy import noise
os.environ.setdefault("SUPPRESS_MOVIEPY_PRINT", "1")

# Redirect moviepy stdout noise during import
import io, contextlib
_silence = io.StringIO()
with contextlib.redirect_stdout(_silence):
    try:
        from agents.video_agent import (
            _build_scene_search_queries,
            _extract_all_characters,
            _extract_opening_location,
            _wikimedia_image_results,
            _search_duckduckgo_images,
            _search_pexels_images,
            _search_pixabay_images,
            _search_flickr_images,
            _search_openverse_images,
            _search_loc_images,
            _search_pexels_videos,
            _search_pixabay_videos,
            _search_duckduckgo_videos,
            _is_arabic_text,
            _translate_arabic_chunk_for_search,
            _SCRIPT_CHARACTER_ORDER,
        )
    except ImportError as e:
        print(f"[ERROR] Import failed: {e}")
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────
SOURCES_IMAGE = [
    ("Wikimedia",   lambda q: _wikimedia_image_results(q, max_results=5)),
    ("DDG",         lambda q: _search_duckduckgo_images(q, max_results=5)),
    ("Pexels",      lambda q: _search_pexels_images(q, max_results=5)),
    ("Pixabay",     lambda q: _search_pixabay_images(q, max_results=5)),
    ("Flickr",      lambda q: _search_flickr_images(q, max_results=5)),
    ("OpenVerse",   lambda q: _search_openverse_images(q, max_results=5)),
    ("LoC",         lambda q: _search_loc_images(q, max_results=5)),
]

SOURCES_VIDEO = [
    ("PexelsVideo",   lambda q: _search_pexels_videos(q, per_page=5)),
    ("PixabayVideo",  lambda q: _search_pixabay_videos(q, per_page=5)),
    ("DDGVideo",      lambda q: _search_duckduckgo_videos(q, max_results=5)),
]

EVENT_TYPES = ["portrait", "evidence", "courtroom", "location", "atmosphere"]

SEP = "-" * 72


def test_queries(topic: str, chunk: str, event_type: str = "evidence") -> list[str]:
    print(f"\n{SEP}")
    print(f"  QUERY BUILDER  topic='{topic}'  chunk='{chunk[:60]}'  type={event_type}")
    print(SEP)
    queries = _build_scene_search_queries(chunk, topic, event_type)
    for i, q in enumerate(queries):
        print(f"  [{i+1:02d}] {q}")
    return queries


def test_source(source_name: str, fn, query: str) -> list[str]:
    t0 = time.time()
    try:
        urls = fn(query) or []
    except Exception as e:
        urls = []
        print(f"  [{source_name:12s}] ERROR: {e}")
        return urls
    elapsed = time.time() - t0
    status = f"{len(urls)} URLs" if urls else "0 (miss)"
    print(f"  [{source_name:12s}] {status:18s}  ({elapsed:.1f}s)  first: {urls[0][:80] if urls else '-'}")
    return urls


def test_all_sources(queries: list[str], include_video: bool = False) -> dict:
    totals = {s: 0 for s, _ in SOURCES_IMAGE}
    if include_video:
        totals.update({s: 0 for s, _ in SOURCES_VIDEO})

    # Test first 3 queries across all image sources
    for q in queries[:3]:
        print(f"\n  Query: \"{q}\"")
        for src, fn in SOURCES_IMAGE:
            urls = test_source(src, fn, q)
            totals[src] += len(urls)

    if include_video:
        print(f"\n  Video sources (first query: \"{queries[0]}\")")
        for src, fn in SOURCES_VIDEO:
            urls = test_source(src, fn, queries[0])
            totals[src] += len(urls)

    return totals


def test_character_extraction(topic: str, script_excerpt: str) -> list[str]:
    print(f"\n{SEP}")
    print(f"  CHARACTER EXTRACTION  topic='{topic}'")
    print(SEP)
    t0 = time.time()
    chars = _extract_all_characters(script_excerpt, topic)
    print(f"  Characters ({time.time()-t0:.1f}s): {chars}")
    return chars


def test_opening_location(topic: str, script_excerpt: str) -> str:
    print(f"\n{SEP}")
    print(f"  OPENING LANDSCAPE  topic='{topic}'")
    print(SEP)
    loc = _extract_opening_location(script_excerpt, topic)
    print(f"  Query: {loc}")
    t0 = time.time()
    urls = _search_pexels_images(loc, max_results=3) or _search_duckduckgo_images(loc, max_results=3)
    print(f"  Pexels/DDG: {len(urls)} URLs ({time.time()-t0:.1f}s)")
    for u in urls[:3]:
        print(f"    {u[:100]}")
    return loc


def test_arabic_translation(arabic_chunk: str) -> None:
    print(f"\n{SEP}")
    print(f"  ARABIC TRANSLATION")
    print(SEP)
    print(f"  Input: {arabic_chunk[:80]}")
    is_ar = _is_arabic_text(arabic_chunk)
    print(f"  Detected Arabic: {is_ar}")
    if is_ar:
        t0 = time.time()
        translated = _translate_arabic_chunk_for_search(arabic_chunk)
        print(f"  Translated ({time.time()-t0:.1f}s): {translated}")
    else:
        print("  (Not Arabic — skipping translation test)")


def print_summary(totals: dict) -> None:
    print(f"\n{SEP}")
    print("  SUMMARY — total URLs found per source")
    print(SEP)
    for src, count in sorted(totals.items(), key=lambda x: -x[1]):
        bar = "#" * min(count, 40)
        print(f"  {src:14s} {count:4d}  {bar}")  # noqa
    grand = sum(totals.values())
    print(f"\n  Total URLs across all sources: {grand}")
    if grand == 0:
        print("  [!] No results — check API keys (PEXELS_API_KEY, PIXABAY_API_KEY) and network")


# ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Test image/video search pipeline")
    parser.add_argument("topic", nargs="?", default="Ted Bundy",
                        help="Topic / main character (default: Ted Bundy)")
    parser.add_argument("--chunk", default="",
                        help="Specific chunk text to test (uses default if empty)")
    parser.add_argument("--type", default="evidence",
                        help="Event type: portrait/evidence/courtroom/location/atmosphere")
    parser.add_argument("--video", action="store_true",
                        help="Also test video sources (Pexels/Pixabay/DDG video)")
    parser.add_argument("--arabic", action="store_true",
                        help="Test Arabic translation with a sample Arabic chunk")
    args = parser.parse_args()

    topic = args.topic
    event_type = args.type
    chunk = args.chunk or f"{topic} arrested trial conviction sentenced"

    eq = "=" * 72
    print(f"\n{eq}")
    print(f"  SEARCH TEST  |  topic: {topic}  |  event: {event_type}")
    print(eq)

    # 1. Opening landscape
    script_excerpt = (
        f"This is the story of {topic}. "
        + {
            "Ted Bundy":       "He lived in Seattle Washington and committed crimes across the Pacific Northwest.",
            "Pablo Escobar":   "He grew up in Medellin Colombia and became the most powerful drug lord in history.",
            "El Chapo":        "He was born in Sinaloa Mexico and ran the Sinaloa cartel.",
            "Jeffrey Dahmer":  "He committed his crimes in Milwaukee Wisconsin.",
            "BTK Killer":      "Dennis Rader terrorized Wichita Kansas for decades.",
            "Griselda Blanco": "She ran the Medellin cocaine trade and later operated from Miami Florida.",
        }.get(topic, f"The story took place across multiple cities and locations.")
    )
    test_opening_location(topic, script_excerpt)

    # 2. Arabic translation test
    if args.arabic:
        arabic_samples = {
            "Pablo Escobar": "باولو إسكوبار اعتُقل في مدينة ميديين في كولومبيا عام 1993 بعد مطاردة طويلة",
            "Ted Bundy":     "تيد باندي اعتُقل في فلوريدا وحكم عليه بالإعدام عام 1989",
        }
        sample = arabic_samples.get(topic, "اعتُقل الشخص المشتبه به في المدينة بعد مطاردة طويلة من قبل السلطات")
        test_arabic_translation(sample)

    # 3. Character extraction
    chars = test_character_extraction(topic, script_excerpt + " " + chunk)

    # 4. Query building — test multiple event types
    all_queries: list[str] = []
    for et in [event_type, "portrait", "location"]:
        q_list = test_queries(topic, chunk, et)
        all_queries.extend(q_list)

    # 5. Source testing — use queries from primary event type
    primary_queries = _build_scene_search_queries(chunk, topic, event_type)
    print(f"\n{SEP}")
    print(f"  SOURCE TEST  (event_type={event_type})")
    print(SEP)
    totals = test_all_sources(primary_queries, include_video=args.video)

    # 6. Summary
    print_summary(totals)

    print(f"\n{eq}\n")


if __name__ == "__main__":
    main()
