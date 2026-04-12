# ============================================================
#  agents/research_agent.py  —  No Anthropic API required
#  Uses DuckDuckGo search + Groq only
# ============================================================
import random
import json
import time
import datetime
from pathlib import Path
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS
import groq as groq_lib
from groq import Groq
from config import GROQ_API_KEY, NICHES, NICHE_WEIGHTS

_groq = Groq(api_key=GROQ_API_KEY)

_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",   # primary
    "llama-3.1-8b-instant",      # fallback
]


def _groq_call(**kwargs):
    """Try each model with one 40-second retry on rate limit before moving to fallback."""
    last_err = None
    for model in _FALLBACK_MODELS:
        for attempt in range(2):
            try:
                time.sleep(3)
                return _groq.chat.completions.create(model=model, **kwargs)
            except groq_lib.RateLimitError as e:
                last_err = e
                if attempt == 0:
                    print(f"[Groq] Rate limit hit — waiting 40 seconds...")
                    time.sleep(40)
                else:
                    print(f"[Groq] Rate limit again on {model}, trying next model...")
                    break
            except groq_lib.BadRequestError as e:
                print(f"[Groq] BadRequestError on {model}, trying next model...")
                last_err = e
                break
    raise last_err

COVERED_TOPICS_PATH = Path("output/covered_topics.json")


# ── DuckDuckGo search helper ────────────────────────────────

def web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo and return concatenated snippet text."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return " ".join(r.get("body", "") for r in results)[:3000] or "(no results)"
    except Exception as e:
        return f"(search error: {e})"


# ── Covered topics tracker ──────────────────────────────────

def _load_covered() -> list[dict]:
    if COVERED_TOPICS_PATH.exists():
        try:
            return json.loads(
                COVERED_TOPICS_PATH.read_text(encoding="utf-8")
            ).get("covered", [])
        except Exception:
            pass
    return []


def _covered_series_set() -> set[str]:
    return {entry["series"].lower() for entry in _load_covered()}


def mark_covered(series: str, video_id: str) -> None:
    """Call after a successful upload to prevent repeating the topic."""
    COVERED_TOPICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    covered = _load_covered()
    covered.append({
        "series": series,
        "date": datetime.date.today().isoformat(),
        "video_id": video_id,
    })
    COVERED_TOPICS_PATH.write_text(
        json.dumps({"covered": covered}, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"[Research] Marked as covered: {series}")


# ── Series discovery (DuckDuckGo + Groq) ───────────────────

def discover_new_series() -> list[str]:
    """Find fresh crime series not yet covered. Returns up to 20 names."""
    already_done = _covered_series_set()

    queries = [
        "best crime series Netflix 2025 2026",
        "top crime movies based on true story IMDB",
        "new true crime documentary 2026",
        "most watched crime series all time",
    ]

    raw_text = ""
    for q in queries:
        raw_text += f"\nQuery: {q}\n{web_search(q)}\n"
        time.sleep(0.3)

    prompt = f"""You are a content researcher. Based on the search results below,
compile a list of 30 unique crime TV series or movies (real titles only).
Include all-time classics and recent 2024-2026 releases.

Search results:
{raw_text[:4000]}

Return ONLY this JSON:
{{
  "series": ["Title 1", "Title 2", "Title 3", ...]
}}"""

    try:
        response = _groq_call(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content.strip())
        all_series = data.get("series", [])
        fresh = [s for s in all_series if s.lower() not in already_done]
        print(f"[Research] Discovered {len(fresh)} fresh series ({len(all_series) - len(fresh)} already covered)")
        return fresh[:20]
    except Exception as e:
        print(f"[Research] Series discovery failed: {e}")

    # Fallback to built-in NICHES
    fallback = []
    for niche in NICHES:
        s = niche.split("behind")[-1].strip() if "behind" in niche else niche
        if s.lower() not in already_done:
            fallback.append(s)
    return fallback


# ── Topic selection ─────────────────────────────────────────

def get_trending_topic(series: str, niche: str) -> dict:
    prompt = f"""You are a viral content strategist for true crime YouTube/TikTok channels.

Series: {series}
Niche: {niche}

Suggest ONE highly specific, curiosity-driven topic angle for a 12-minute documentary.
The topic must be about REAL historical facts behind {series}.

Return ONLY this JSON:
{{
  "topic": "Specific real-world topic about {series}",
  "angle": "The shocking or surprising angle that hooks viewers",
  "keywords": ["{series}", "crime", "real story"],
  "search_query": "crime dark night investigation"
}}"""

    response = _groq_call(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=500,
        response_format={"type": "json_object"}
    )
    result = json.loads(response.choices[0].message.content.strip())
    result["niche"] = niche
    result["series"] = series
    return result


def research_topics(count: int = 2, niches: list[str] | None = None) -> list[dict]:
    """Discover fresh topics, filter covered, pick best, generate angles.

    Args:
        count:  Number of topics to return.
        niches: Optional explicit niche list (overrides the config NICHES and
                skips the Dark Crime series discovery flow entirely).  Pass
                config_shopmart.NICHES when calling from run_shopmart.py.
    """
    covered = _covered_series_set()

    # ── Shopmart / non-crime path: pick directly from caller-supplied niches ──
    if niches is not None:
        available = [n for n in niches if n.lower() not in covered]
        if not available:
            available = list(niches)          # recycle if all covered
        random.shuffle(available)
        selected_niches = available[:count]
        topics = []
        for niche in selected_niches:
            topic = get_trending_topic(niche, niche)
            topics.append(topic)
            print(f"[Research] Found topic: {topic['topic']} ({niche})")
        return topics

    # ── Dark Crime path: discover series via DuckDuckGo ──────────────────────
    fresh_series = discover_new_series()

    if not fresh_series:
        fresh_series = [
            niche.split("behind")[-1].strip() if "behind" in niche else niche
            for niche in NICHES
            if (niche.split("behind")[-1].strip() if "behind" in niche else niche).lower() not in covered
        ]

    random.shuffle(fresh_series)
    selected = fresh_series[:count]

    if not selected:
        print("[Research] All known series covered — recycling oldest topics")
        all_series = [
            niche.split("behind")[-1].strip() if "behind" in niche else niche
            for niche in NICHES
        ]
        random.shuffle(all_series)
        selected = all_series[:count]

    topics = []
    for series in selected:
        niche = next(
            (n for n in NICHES if series.lower() in n.lower()),
            f"True crime — real story behind {series}"
        )
        topic = get_trending_topic(series, niche)
        topics.append(topic)
        print(f"[Research] Found topic: {topic['topic']} ({niche})")

    return topics


# ── Deep research on a specific series ─────────────────────

def research_series(series_name: str) -> dict:
    """Search DuckDuckGo then use Groq to extract structured facts for the script."""
    print(f"[Research] Searching web for: {series_name}")

    def _search(query: str, max_results: int = 5) -> str:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return " ".join(r.get("body", "") for r in results if r.get("body"))[:2000]
        except Exception as e:
            print(f"[Research] DDG error: {e}")
            return ""

    raw_facts    = _search(f"{series_name} real true story historical facts", 5)
    raw_wrong    = _search(f"{series_name} what show got wrong inaccurate dramatized", 3)
    raw_shocking = _search(f"{series_name} most shocking real facts untold story", 3)

    prompt = f"""You are a fact-checker for a true crime documentary channel.
Based on the search results below about "{series_name}", extract verified facts.

Facts about the real story:
{raw_facts}

What the show got wrong:
{raw_wrong}

Shocking real details:
{raw_shocking}

Return ONLY this JSON:
{{
  "research_facts": [
    "Specific confirmed fact 1 with real dates/names",
    "Specific confirmed fact 2 with real dates/names",
    "Specific confirmed fact 3 with real dates/names",
    "Specific confirmed fact 4 with real dates/names",
    "Specific confirmed fact 5 with real dates/names"
  ],
  "research_inaccuracies": [
    "What the show got wrong or dramatized #1",
    "What the show got wrong or dramatized #2",
    "What the show got wrong or dramatized #3"
  ],
  "research_shocking": [
    "Most shocking real fact viewers don't know #1",
    "Most shocking real fact viewers don't know #2",
    "Most shocking real fact viewers don't know #3"
  ]
}}"""

    try:
        response = _groq_call(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content.strip())
        facts_out    = data.get("research_facts", [])
        wrong_out    = data.get("research_inaccuracies", [])
        shocking_out = data.get("research_shocking", [])
        print(f"[Research] Extracted {len(facts_out)} facts, {len(wrong_out)} inaccuracies, {len(shocking_out)} shocking — {series_name}")
    except Exception as e:
        print(f"[Research] Groq extraction failed: {e} — using raw snippets")
        facts_out    = [raw_facts[:400]]    if raw_facts    else []
        wrong_out    = [raw_wrong[:400]]    if raw_wrong    else []
        shocking_out = [raw_shocking[:400]] if raw_shocking else []

    return {
        "series":                        series_name,
        # Primary fields used by the new script prompt
        "research_facts":                facts_out,
        "research_inaccuracies":         wrong_out,
        "research_shocking":             shocking_out,
        # Legacy fields kept for backward compatibility
        "real_story":                    raw_facts,
        "what_show_got_right":           facts_out[:3],
        "what_show_got_wrong":           wrong_out,
        "shocking_real_facts":           shocking_out,
        "real_people_behind_characters": {},
    }
